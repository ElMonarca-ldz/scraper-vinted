import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

# Ensure data directory exists
DATA_DIR = '/app/data'
# Fallback for local development if not running in container structure
if not os.path.exists(DATA_DIR):
    # Check if we are potentially on windows local dev
    if os.name == 'nt':
         DATA_DIR = 'data'
    else:
         # In linux but maybe not in container, try to create or fallback
         try:
             os.makedirs(DATA_DIR, exist_ok=True)
         except PermissionError:
             DATA_DIR = 'data'

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, 'vinted.db')
DATABASE_URL = f"sqlite:///{DB_PATH}"

Base = declarative_base()

    def __repr__(self):
        return f"<SearchConfig(term='{self.term}')>"

class ScraperLog(Base):
    __tablename__ = 'scraper_logs'
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    level = Column(String) # INFO, ERROR, WARNING
    message = Column(String)
    
    def __repr__(self):
        return f"<Log {self.timestamp}: {self.message}>"

class SearchConfig(Base):
    __tablename__ = 'search_configs'
    
    id = Column(Integer, primary_key=True)
    term = Column(String, nullable=False)
    min_price = Column(Float, nullable=True)
    max_price = Column(Float, nullable=True)
    sizes = Column(String, nullable=True) 
    condition = Column(String, nullable=True) # New column for comma-separated condition IDs
    last_run = Column(DateTime, nullable=True)
    
    products = relationship("Product", back_populates="search_config", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<SearchConfig(term='{self.term}')>"

class Product(Base):
    __tablename__ = 'products'
    
    id = Column(Integer, primary_key=True)
    search_config_id = Column(Integer, ForeignKey('search_configs.id'))
    title = Column(String)
    brand = Column(String)
    price = Column(Float)
    size = Column(String)
    url = Column(String, unique=True)
    image_url = Column(String, nullable=True)
    scanned_at = Column(DateTime, default=datetime.utcnow)
    
    search_config = relationship("SearchConfig", back_populates="products")

    def __repr__(self):
        return f"<Product(title='{self.title}', price={self.price})>"

# Setup Database
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    
    # Auto-migration for 'condition' column if it doesn't exist
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    columns = [c['name'] for c in inspector.get_columns('search_configs')]
    
    if 'condition' not in columns:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE search_configs ADD COLUMN condition VARCHAR"))
            conn.commit()
            
    # Auto-migration for 'ScraperLog' table (handled by create_all usually, but good to be sure)
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
