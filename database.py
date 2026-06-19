import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
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

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
