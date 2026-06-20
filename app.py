import os
import json
import base64
import queue
import io
import asyncio
from datetime import datetime
import pandas as pd
from typing import List, Optional
from fastapi import FastAPI, Depends, BackgroundTasks, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import database
from database import init_db, get_db, DbFile, CategoryConfig, SizeMapping, ProcessingJob, Setting, DbErrorLog
import mapping_engine
from mapping_engine import EngineLogger, learn_from_historical_excel, generate_nykaa_template
from fastapi import Request
from fastapi.responses import JSONResponse
import traceback

# Initialize FastAPI App
app = FastAPI(title="Nykaa Auto Lister Pro")

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_msg = str(exc)
    stack_trace = traceback.format_exc()
    
    print(f"ERROR: {error_msg}\n{stack_trace}")
    
    try:
        db = next(get_db())
        log_entry = DbErrorLog(
            endpoint=str(request.url),
            error_message=error_msg,
            stack_trace=stack_trace
        )
        db.add(log_entry)
        db.commit()
    except Exception as db_err:
        print(f"Failed to log error to DB: {db_err}")
        
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal Server Error: {error_msg}"}
    )


# Server-Sent Events log queue
global_log_listeners = []

class SSELogger:
    def __init__(self, job_id=None):
        self.job_id = job_id
        
    def log(self, level, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {
            "time": timestamp,
            "level": level.upper(),
            "message": message,
            "job_id": self.job_id
        }
        print(f"[{level.upper()}] {message}")
        for q in global_log_listeners:
            try:
                q.put_nowait(log_entry)
            except Exception:
                pass

# Database Initialization on Startup
@app.on_event("startup")
def startup_event():
    init_db()
    print("Database tables initialized successfully.")
    
    # Auto-migrate dump file if it exists
    dump_path = "migration_dump.json"
    if os.path.exists(dump_path):
        print("Found migration_dump.json. Checking if database import is needed...")
        try:
            with open(dump_path, "r") as f:
                data = json.load(f)
                
            db = database.SessionLocal()
            
            # Check if we have configs already
            existing_configs_count = db.query(CategoryConfig).count()
            if existing_configs_count == 0:
                print("Database is empty. Importing dump data...")
                
                # 1. Import db_files
                file_id_map = {}
                for f_data in data.get("db_files", []):
                    # Check if filename already exists
                    exists = db.query(DbFile).filter(DbFile.filename == f_data["filename"], DbFile.file_type == f_data["file_type"]).first()
                    if not exists:
                        db_file = DbFile(
                            file_type=f_data["file_type"],
                            filename=f_data["filename"],
                            content_b64=f_data["content_b64"],
                            is_active=f_data.get("is_active", False)
                        )
                        if f_data.get("uploaded_at"):
                            db_file.uploaded_at = datetime.fromisoformat(f_data["uploaded_at"])
                        db.add(db_file)
                        db.flush()
                        file_id_map[f_data["id"]] = db_file.id
                    else:
                        file_id_map[f_data["id"]] = exists.id
                        
                db.commit()
                
                # 2. Import category_configs
                for c_data in data.get("category_configs", []):
                    new_template_id = file_id_map.get(c_data["template_file_id"]) if c_data.get("template_file_id") else None
                    new_config = CategoryConfig(
                        category_name=c_data["category_name"],
                        template_file_id=new_template_id,
                        hardcoded_values=c_data["hardcoded_values"],
                        column_mappings=c_data["column_mappings"]
                    )
                    db.add(new_config)
                    
                # 3. Import size_mappings
                for s_data in data.get("size_mappings", []):
                    new_size = SizeMapping(
                        category_name=s_data["category_name"],
                        brand_size=s_data["brand_size"],
                        measurements=s_data["measurements"]
                    )
                    db.add(new_size)
                    
                db.commit()
                print("Successfully imported configurations and size chart data on startup!")
            else:
                print("Database already contains data. Skipping auto-import.")
            db.close()
        except Exception as import_err:
            print(f"Failed to auto-import migration dump: {str(import_err)}")

# SSE logs stream connection endpoint
@app.get("/api/logs")
def get_logs_stream():
    q = queue.Queue(maxsize=100)
    global_log_listeners.append(q)
    
    async def sse_event_generator():
        try:
            # Yield initial connection confirmation
            import datetime
            yield f"data: {json.dumps({'time': datetime.datetime.now().strftime('%H:%M:%S'), 'level': 'INFO', 'message': 'Log Console Connected'})}\n\n"
            
            while True:
                # Check for log entries
                while not q.empty():
                    log_entry = q.get_nowait()
                    yield f"data: {json.dumps(log_entry)}\n\n"
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        finally:
            if q in global_log_listeners:
                global_log_listeners.remove(q)
                
    return StreamingResponse(sse_event_generator(), media_type="text/event-stream")

def run_bg_learning(file_id: int, file_type: str, filename: str, content_bytes: bytes, content_b64: str):
    db = database.SessionLocal()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content_bytes), read_only=True)
        category = None
        for sname in wb.sheetnames:
            if sname not in ["Instructions Sheet", "Instructions", "mastersheet", "masterdata"]:
                category = sname.strip()
                break
        wb.close()
        
        if category:
            # Link configuration
            config = db.query(CategoryConfig).filter(CategoryConfig.category_name == category).first()
            if not config:
                config = CategoryConfig(category_name=category)
                db.add(config)
                
            db_file = db.query(DbFile).filter(DbFile.id == file_id).first()
            if db_file and file_type == "category_template":
                config.template_file_id = db_file.id
                
            db.commit()
            
            # Auto-run learn from this sheet
            q = queue.Queue()
            logger = EngineLogger(job_id="learning-auto", log_queue=q)
            learn_from_historical_excel(db, content_b64, filename, category, logger)
    except Exception as e:
        print(f"Background auto-learning failed: {e}")
    finally:
        db.close()

# Upload files to database storage
@app.post("/api/upload")
def upload_file(
    file_type: str = Form(...),
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    try:
        content_bytes = file.file.read()
        content_b64 = database.compress_and_encode(content_bytes)
        
        # Save to database
        db_file = DbFile(
            file_type=file_type,
            filename=file.filename,
            content_b64=content_b64,
            is_active=False
        )
        db.add(db_file)
        db.commit()

        # Run heavy auto-parsing and learning asynchronously in the background
        if file_type in ["category_template", "historical_listing"] and background_tasks:
            background_tasks.add_task(
                run_bg_learning,
                db_file.id,
                file_type,
                file.filename,
                content_bytes,
                content_b64
            )

        return {"id": db_file.id, "filename": db_file.filename, "file_type": db_file.file_type}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

# Get files catalog
@app.get("/api/files")
def list_files(db: Session = Depends(get_db)):
    files = db.query(DbFile).order_by(DbFile.uploaded_at.desc()).all()
    
    # Identify the latest ID for each file type to determine which is currently active/latest
    latest_ids = {}
    for ftype in ['item_directory', 'content_sheet', 'category_template', 'historical_listing']:
        latest = db.query(DbFile).filter(DbFile.file_type == ftype).order_by(DbFile.uploaded_at.desc()).first()
        if latest:
            latest_ids[ftype] = latest.id
            
    return [{
        "id": f.id,
        "file_type": f.file_type,
        "filename": f.filename,
        "is_latest": f.id == latest_ids.get(f.file_type),
        "uploaded_at": f.uploaded_at.isoformat()
    } for f in files]

# Delete uploaded files from database
@app.delete("/api/files/delete")
def delete_file(file_id: int, db: Session = Depends(get_db)):
    target = db.query(DbFile).filter(DbFile.id == file_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="File not found")
    db.delete(target)
    db.commit()
    return {"message": f"Successfully deleted file: {target.filename}"}

# Trigger learning engine on template or historical file
@app.post("/api/learn-historical")
def learn_historical(file_id: int, category: str, db: Session = Depends(get_db)):
    target = db.query(DbFile).filter(DbFile.id == file_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="File not found")
        
    q = queue.Queue()
    global_log_listeners.append(q)
    logger = EngineLogger(job_id="learning", log_queue=q)
    
    try:
        learn_from_historical_excel(db, target.content_b64, target.filename, category, logger)
        
        # Link this file as the category template if file_type is 'category_template'
        if target.file_type == "category_template":
            config = db.query(CategoryConfig).filter(CategoryConfig.category_name == category).first()
            if not config:
                config = CategoryConfig(category_name=category)
                db.add(config)
            config.template_file_id = target.id
            db.commit()
            
        return {"message": f"Learned mappings from '{target.filename}' successfully!"}
    except Exception as e:
        logger.log("error", f"Learning failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if q in global_log_listeners:
            global_log_listeners.remove(q)

# Get Category configs A-Z
@app.get("/api/categories")
def list_categories(db: Session = Depends(get_db)):
    configs = db.query(CategoryConfig).all()
    # Ensure default list covers several categories
    all_known = ["Bedsheets", "Comforters", "Dohars", "Blankets", "Curtains", "Cushion Covers", "Towels", "Rugs", "Mats", "Kitchen Linen", "Home Furnishing", "Apparel", "Footwear", "Bags", "Accessories", "Tshirts", "Westernwear Dresses", "shorts", "Trousers", "Kids Clothing Core"]
    
    for c in configs:
        if c.category_name and c.category_name not in all_known:
            all_known.append(c.category_name)
            
    out = []
    configs_dict = {c.category_name: c for c in configs}
    
    for cat in sorted(list(set(all_known))):
        c = configs_dict.get(cat)
        out.append({
            "category_name": cat,
            "has_template": c.template_file_id is not None if c else False,
            "template_file_name": c.template_file.filename if c and c.template_file else "",
            "hardcoded_count": len(c.hardcoded_values) if c else 0,
            "hardcoded_values": c.hardcoded_values if c else {},
            "column_mappings": c.column_mappings if c else {}
        })
    return out

# Update category configurations
@app.post("/api/categories/update")
def update_category(
    category_name: str,
    hardcoded_values: str = Form(None),
    db: Session = Depends(get_db)
):
    config = db.query(CategoryConfig).filter(CategoryConfig.category_name == category_name).first()
    if not config:
        config = CategoryConfig(category_name=category_name)
        db.add(config)
        
    if hardcoded_values:
        try:
            config.hardcoded_values = json.loads(hardcoded_values)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON format for hardcoded values")
            
    db.commit()
    return {"message": "Category config updated successfully"}

# Upload size chart Excel mappings
@app.post("/api/sizecharts/upload-excel")
def upload_sizechart_excel(
    category: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        content_bytes = file.file.read()
        df = pd.read_excel(io.BytesIO(content_bytes))
        
        # The Excel sheet should contain columns:
        # brand  size, Chest, Waist, Hip, Length, etc.
        if df.empty or len(df.columns) < 2:
            raise ValueError("Size chart sheet is empty or lacks required columns.")
            
        # Standardize columns
        columns = df.columns.tolist()
        size_col = None
        for col in columns:
            if "size" in str(col).lower() or "brand" in str(col).lower():
                size_col = col
                break
        if not size_col:
            size_col = columns[0]
            
        # Clear existing mappings
        db.query(SizeMapping).filter(SizeMapping.category_name == category).delete()
        
        count = 0
        for _, row in df.iterrows():
            size_label = str(row[size_col]).strip()
            if not size_label or size_label == "nan":
                continue
                
            measurements = {}
            for col in columns:
                if col != size_col:
                    val = row[col]
                    if pd.notna(val):
                        # Convert float integers
                        if isinstance(val, float) and val.is_integer():
                            val = int(val)
                        measurements[str(col).strip()] = val
                        
            sz = SizeMapping(
                category_name=category,
                brand_size=size_label,
                measurements=measurements
            )
            db.add(sz)
            count += 1
            
        db.commit()
        return {"message": f"Successfully parsed and loaded {count} sizes for '{category}'."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse size chart: {str(e)}")

# Get Sizing chart mapping data
@app.get("/api/sizecharts")
def get_sizecharts(category: str, db: Session = Depends(get_db)):
    sizes = db.query(SizeMapping).filter(SizeMapping.category_name == category).all()
    return [{
        "id": s.id,
        "brand_size": s.brand_size,
        "measurements": s.measurements
    } for s in sizes]

# Update size mapping cell manually
@app.post("/api/sizecharts/update")
def update_sizechart(
    category: str,
    brand_size: str,
    measurements: str = Form(...),
    db: Session = Depends(get_db)
):
    try:
        meas_dict = json.loads(measurements)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON format for measurements")
        
    sz = db.query(SizeMapping).filter(
        SizeMapping.category_name == category,
        SizeMapping.brand_size == brand_size
    ).first()
    
    if not sz:
        sz = SizeMapping(category_name=category, brand_size=brand_size)
        db.add(sz)
        
    sz.measurements = meas_dict
    db.commit()
    return {"message": "Size mapping updated successfully"}

# Background generator worker launcher
def run_listing_generation_task(job_id: str, db_session_factory):
    db: Session = db_session_factory()
    job = db.query(ProcessingJob).filter(ProcessingJob.job_id == job_id).first()
    if not job:
        db.close()
        return
        
    # Bind Server log queue
    q = queue.Queue()
    global_log_listeners.append(q)
    logger = EngineLogger(job_id=job_id, log_queue=q)
    
    try:
        generate_nykaa_template(db, job, logger)
    except Exception as e:
        import traceback
        err_msg = str(e)
        logger.log("error", f"Generation failed: {err_msg}")
        logger.log("error", traceback.format_exc())
        job.status = "failed"
        job.progress = 100
        job.validation_report = {"error": err_msg}
        db.commit()
    finally:
        if q in global_log_listeners:
            global_log_listeners.remove(q)
        db.close()

# Start generation API
@app.post("/api/run")
def start_generation(
    category: str = Form(...),
    input_codes: str = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    import time
    job_id = str(int(time.time()))
    
    job = ProcessingJob(
        job_id=job_id,
        status="running",
        progress=10,
        category=category,
        input_codes=input_codes
    )
    db.add(job)
    db.commit()
    
    # Run in FastAPI background thread
    background_tasks.add_task(run_listing_generation_task, job_id, database.SessionLocal)
    return {"job_id": job_id, "message": "Listing generation initiated."}

# Get generator job statuses
@app.get("/api/status")
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter(ProcessingJob.job_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "category": job.category,
        "output_filename": job.output_filename,
        "output_file_id": job.output_file_id,
        "validation_report": job.validation_report,
        "created_at": job.created_at.isoformat()
    }

# File Download Endpoint (loads from DB storage and returns response stream)
@app.get("/api/download")
def download_file(file_id: int, db: Session = Depends(get_db)):
    db_file = db.query(DbFile).filter(DbFile.id == file_id).first()
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found")
        
    file_bytes = database.decode_and_decompress(db_file.content_b64)
    filename = db_file.filename
    file_type = db_file.file_type
    
    # If this is a generated output file, delete it from the database immediately to save space
    if file_type == "output_file":
        db.delete(db_file)
        db.commit()
        
    stream = io.BytesIO(file_bytes)
    
    # Return streaming download response
    headers = {
        'Content-Disposition': f'attachment; filename="{filename}"'
    }
    
    # Resolve MIME type
    media_type = "application/octet-stream"
    if filename.endswith(".xlsx"):
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif filename.endswith(".xlsm"):
        media_type = "application/vnd.ms-excel.sheet.macroEnabled.12"
    elif filename.endswith(".csv"):
        media_type = "text/csv"
        
    return StreamingResponse(stream, media_type=media_type, headers=headers)

# Settings get/set
@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    settings = db.query(Setting).all()
    return {s.key: s.value for s in settings}

@app.post("/api/settings")
def update_settings(settings_dict: dict, db: Session = Depends(get_db)):
    for k, v in settings_dict.items():
        s = db.query(Setting).filter(Setting.key == k).first()
        if not s:
            s = Setting(key=k)
            db.add(s)
        s.value = str(v)
    db.commit()
    return {"message": "Settings updated successfully"}

# serving frontend
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    # Start ASGI server
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
