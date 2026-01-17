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

class ScraperLog(Base):
    __tablename__ = 'scraper_logs'
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    level = Column(String) # INFO, ERROR, WARNING
    message = Column(String)
    
    def __repr__(self):
        return f"<Log {self.timestamp}: {self.message}>"

class PriceHistory(Base):
    __tablename__ = 'price_history'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'))
    price = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    product = relationship("Product", back_populates="price_history")

class Config(Base):
    __tablename__ = 'config'
    # Singleton table for App Settings
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    value = Column(String)

class Brand(Base):
    __tablename__ = 'brands'
    
    id = Column(Integer, primary_key=True)
    vinted_id = Column(String, unique=True)
    title = Column(String)
    is_favorite = Column(Integer, default=0) # 0=No, 1=Yes

class AlertRule(Base):
    __tablename__ = 'alert_rules'
    
    id = Column(Integer, primary_key=True)
    name = Column(String)
    brand_list = Column(String, nullable=True) # Comma separated names
    max_price = Column(Float, nullable=True)
    min_discount_percent = Column(Float, nullable=True) # % below fair price
    min_z_score = Column(Float, nullable=True) # e.g. -1.5
    is_active = Column(Integer, default=1)

class SearchConfig(Base):
    __tablename__ = 'search_configs'
    
    id = Column(Integer, primary_key=True)
    term = Column(String)
    brand_name = Column(String) # Text filter or DB synced name
    min_price = Column(Float)
    max_price = Column(Float)
    sizes = Column(String) # Comma separated IDs
    condition = Column(String)
    color_ids = Column(String)
    catalog_ids = Column(String)
    
    # Limits
    max_pages = Column(Integer, default=5)
    max_items = Column(Integer, default=100)
    
    last_run = Column(DateTime)
    last_check_sold = Column(DateTime)
    
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
    local_image_path = Column(String, nullable=True) # New: Path to local AVIF file
    
    is_sold = Column(Integer, default=0) # 0=Active, 1=Sold
    sold_at = Column(DateTime, nullable=True)
    scanned_at = Column(DateTime, default=datetime.utcnow)
    
    search_config = relationship("SearchConfig", back_populates="products")
    price_history = relationship("PriceHistory", back_populates="product", cascade="all, delete-orphan")

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
    
    # 1. SearchConfig Migrations
    sc_columns = [c['name'] for c in inspector.get_columns('search_configs')]
    
    with engine.connect() as conn:
        if 'condition' not in sc_columns:
            conn.execute(text("ALTER TABLE search_configs ADD COLUMN condition VARCHAR"))
        if 'color_ids' not in sc_columns:
            conn.execute(text("ALTER TABLE search_configs ADD COLUMN color_ids VARCHAR"))
        if 'catalog_ids' not in sc_columns:
            conn.execute(text("ALTER TABLE search_configs ADD COLUMN catalog_ids VARCHAR"))
        if 'brand_name' not in sc_columns:
            conn.execute(text("ALTER TABLE search_configs ADD COLUMN brand_name VARCHAR"))
        
        # Phase 3 Limits
        if 'max_pages' not in sc_columns:
            conn.execute(text("ALTER TABLE search_configs ADD COLUMN max_pages INTEGER DEFAULT 5"))
        if 'max_items' not in sc_columns:
            conn.execute(text("ALTER TABLE search_configs ADD COLUMN max_items INTEGER DEFAULT 100"))
        
        # 2. Product Migrations
        p_columns = [c['name'] for c in inspector.get_columns('products')]
    
    with engine.connect() as conn:
        if 'local_image_path' not in p_columns:
             conn.execute(text("ALTER TABLE products ADD COLUMN local_image_path VARCHAR"))
        if 'is_sold' not in p_columns:
            # SQLite doesn't support adding columns with default values easily in one go if not strict, but basic works
             conn.execute(text("ALTER TABLE products ADD COLUMN is_sold INTEGER DEFAULT 0"))
        if 'sold_at' not in p_columns:
             conn.execute(text("ALTER TABLE products ADD COLUMN sold_at DATETIME"))
        conn.commit()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
