import os
import zlib
import base64
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Try to load environment variables from a .env file manually to support local setup on other computers
if os.path.exists(".env"):
    try:
        with open(".env", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    key_val = line.split("=", 1)
                    if len(key_val) == 2:
                        k, v = key_val[0].strip(), key_val[1].strip()
                        # Strip quotes if present
                        if v.startswith('"') and v.endswith('"'):
                            v = v[1:-1]
                        elif v.startswith("'") and v.endswith("'"):
                            v = v[1:-1]
                        os.environ[k] = v
    except Exception as e:
        print(f"Warning: Failed to parse .env file manually: {e}")

def compress_and_encode(data_bytes: bytes) -> str:
    """Compress bytes using zlib and encode to base64 string.
    Bypasses compression for ZIP/Excel files (which start with PK\\x03\\x04) to save CPU/time,
    and uses fast compression (level 1) otherwise."""
    if data_bytes.startswith(b"PK\x03\x04"):
        compressed = zlib.compress(data_bytes, level=0)
    else:
        compressed = zlib.compress(data_bytes, level=1)
    return base64.b64encode(compressed).decode("utf-8")

def decode_and_decompress(b64_str: str) -> bytes:
    """Decode base64 string and decompress using zlib. Falls back to raw bytes if not compressed."""
    try:
        raw_bytes = base64.b64decode(b64_str)
        try:
            return zlib.decompress(raw_bytes)
        except zlib.error:
            # If it is not compressed (old format), return the raw bytes directly
            return raw_bytes
    except Exception:
        # Fallback in case base64 decode itself fails (should not happen for valid content)
        if isinstance(b64_str, str):
            return b64_str.encode("utf-8")
        return b64_str

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()  # strip() removes any \n or spaces from env vars
if DATABASE_URL:
    # 1. Standardize scheme and auto-encode password if it contains special URL characters (like '@')
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    if DATABASE_URL.startswith("postgresql://"):
        import urllib.parse
        rest = DATABASE_URL[13:]
        if '@' in rest:
            parts = rest.rsplit('@', 1)
            user_pass = parts[0]
            host_db = parts[1]
            if ':' in user_pass:
                user_parts = user_pass.split(':', 1)
                user = user_parts[0]
                password = user_parts[1]
                # Decode first to prevent double-encoding, then safely quote special characters
                decoded_password = urllib.parse.unquote(password)
                encoded_password = urllib.parse.quote(decoded_password)
                DATABASE_URL = f"postgresql://{user}:{encoded_password}@{host_db}"
    
    # 2. Auto-rewrite direct Supabase IPv6 hosts to IPv4 connection pooler to prevent "Network is unreachable"
    if "supabase.co" in DATABASE_URL or "pooler.supabase.com" in DATABASE_URL:
        # Swap direct hostname with the verified pooler hostname
        if "db.iuvdyogawvuorvnzuaxc.supabase.co" in DATABASE_URL:
            DATABASE_URL = DATABASE_URL.replace("db.iuvdyogawvuorvnzuaxc.supabase.co", "aws-1-ap-northeast-1.pooler.supabase.com", 1)
        
        # Ensure we connect via port 6543 (transaction pooler) instead of 5432 (direct/IPv6-only)
        if ":5432" in DATABASE_URL:
            DATABASE_URL = DATABASE_URL.replace(":5432", ":6543", 1)
        elif "pooler.supabase.com/postgres" in DATABASE_URL:
            DATABASE_URL = DATABASE_URL.replace("pooler.supabase.com/postgres", "pooler.supabase.com:6543/postgres", 1)
            
        # Ensure username format contains project reference suffix for pooler validation
        if "postgresql://postgres:" in DATABASE_URL:
            DATABASE_URL = DATABASE_URL.replace("postgresql://postgres:", "postgresql://postgres.iuvdyogawvuorvnzuaxc:", 1)
else:
    # Fallback to SQLite for easy local testing when no cloud PostgreSQL is configured
    DATABASE_URL = "sqlite:///nykaa_autolister.db"

print(f"Initializing database connection: {DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL}")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
else:
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)

class DbFile(Base):
    __tablename__ = "db_files"
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_type = Column(String(50), nullable=False) # 'item_directory', 'content_sheet', 'category_template', 'historical_listing', 'output_file', 'size_chart'
    filename = Column(String(255), nullable=False)
    content_b64 = Column(Text, nullable=False) # File contents encoded in base64
    is_active = Column(Boolean, default=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

class CategoryConfig(Base):
    __tablename__ = "category_configs"
    category_name = Column(String(100), primary_key=True)
    template_file_id = Column(Integer, ForeignKey("db_files.id", ondelete="SET NULL"), nullable=True)
    hardcoded_values = Column(JSON, default=dict) # Mappings of column -> hardcoded value
    column_mappings = Column(JSON, default=dict)  # Nykaa Header -> Item Directory/Content Sheet column
    
    template_file = relationship("DbFile")

class SizeMapping(Base):
    __tablename__ = "size_mappings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category_name = Column(String(100), nullable=False)
    brand_size = Column(String(50), nullable=False)
    measurements = Column(JSON, default=dict) # size chart parameters (e.g. {"Chest": 36})

class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    job_id = Column(String(100), primary_key=True)
    status = Column(String(50), default="running") # 'running', 'success', 'failed'
    progress = Column(Integer, default=0) # 0 to 100
    category = Column(String(100), nullable=False)
    input_codes = Column(Text, nullable=True)
    output_filename = Column(String(255), nullable=True)
    output_file_id = Column(Integer, ForeignKey("db_files.id", ondelete="SET NULL"), nullable=True)
    validation_report = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    output_file = relationship("DbFile")
class DbErrorLog(Base):
    __tablename__ = "db_error_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    endpoint = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)

db_connection_error = None

def init_db():
    global db_connection_error
    try:
        Base.metadata.create_all(bind=engine)
        db_connection_error = None
    except Exception as e:
        db_connection_error = str(e)
        raise e

def get_db():
    if db_connection_error:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail=f"Database connection failed: {db_connection_error}. Please ensure your Supabase project is active/resumed, database credentials in DATABASE_URL are correct, and 0.0.0.0 IP access is allowed in Supabase settings."
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
