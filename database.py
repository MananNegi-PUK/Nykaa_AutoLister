import os
import zlib
import base64
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

def compress_and_encode(data_bytes: bytes) -> str:
    """Compress bytes using zlib and encode to base64 string."""
    compressed = zlib.compress(data_bytes, level=9)
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
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    # Auto-rewrite direct Supabase IPv6 hosts to IPv4 connection pooler to prevent "Network is unreachable"
    if "@db.iuvdyogawvuorvnzuaxc.supabase.co" in DATABASE_URL:
        # Swap direct hostname with the verified pooler hostname
        DATABASE_URL = DATABASE_URL.replace("@db.iuvdyogawvuorvnzuaxc.supabase.co", "@aws-1-ap-northeast-1.pooler.supabase.com", 1)
        # Update username format for the pooler: postgres -> postgres.iuvdyogawvuorvnzuaxc
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

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
