import streamlit as st
import pandas as pd
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
import threading
import logging

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
import threading
import logging
import os

from database import get_db, init_db, SearchConfig, Product, engine, ScraperLog, Config, PriceHistory
from scraper import scrape_vinted, VINTED_SIZE_IDS, VINTED_CONDITION_IDS, VINTED_COLOR_IDS, VINTED_CATALOG_IDS, send_telegram_alert, download_image_as_avif, verify_sold_status

# --- CONFIGURATION ---
st.set_page_config(page_title="Scraper Vinted Pro", layout="wide", page_icon="⚡")

# Initialize DB
init_db()

# --- SCHEDULER SETUP ---
if 'scheduler' not in st.session_state:
    st.session_state.scheduler = BackgroundScheduler()
    st.session_state.scheduler.start()
    logging.info("Scheduler started.")

# Dynamic Job Management
def update_scheduler_job(db: Session):
    scheduler = st.session_state.scheduler
    # Remove existing
    if scheduler.get_job('main_scan'):
        scheduler.remove_job('main_scan')
    if scheduler.get_job('sold_check'):
        scheduler.remove_job('sold_check')
        
    # Get settings
    active_setting = db.query(Config).filter_by(key="scheduler_active").first()
    interval_setting = db.query(Config).filter_by(key="scheduler_interval").first()
    
    is_active = active_setting.value == "1" if active_setting else True
    interval = int(interval_setting.value) if interval_setting else 6
    
    if is_active:
        scheduler.add_job(run_scheduled_scans, 'interval', hours=interval, id='main_scan')
        # Sold check runs every 24h by default, or 2x interval
        scheduler.add_job(run_sold_check_job, 'interval', hours=24, id='sold_check')
        logging.info(f"Scheduler updated: Active={is_active}, ScanInterval={interval}h")
    else:
        logging.info("Scheduler paused.")

def run_scheduled_scans():
    logging.info("Starting scheduled scan...")
    db = next(get_db())
    configs = db.query(SearchConfig).all()
    for config in configs:
        scrape_and_save(db, config)
    db.close()
    logging.info("Scan completed.")

def run_sold_check_job():
    logging.info("Starting Sold Status Check...")
    db = next(get_db())
    # Check last 50 scanned items that are NOT sold
    products = db.query(Product).filter(Product.is_sold == 0).order_by(Product.scanned_at.desc()).limit(50).all()
    
    for p in products:
        status = verify_sold_status(p.url)
        if status == 'sold':
            p.is_sold = 1
            p.sold_at = datetime.utcnow()
            logging.info(f"Item marked as SOLD: {p.title}")
        elif status == 'deleted':
            # Option: Mark as sold or delete? Let's mark as sold/inactive
            p.is_sold = 1 
            logging.info(f"Item DELETED: {p.title}")
            
    db.commit()
    db.close()
    logging.info("Sold Status Check completed.")

# Ensure job exists on startup
if not st.session_state.scheduler.get_jobs():
    # Default fallback
    st.session_state.scheduler.add_job(run_scheduled_scans, 'interval', hours=6, id='main_scan')
    st.session_state.scheduler.add_job(run_sold_check_job, 'interval', hours=24, id='sold_check')

# --- LOGIC ---

def get_fair_price(db, term):
    # Calculate avg price of last 50 items for this term
    # This is a heuristic approximation
    # Ideally filtering by product_id if price history exists, but term-based is good enough for broad view
    stmt = f"SELECT AVG(price) FROM products JOIN search_configs ON products.search_config_id = search_configs.id WHERE search_configs.term = '{term}'"
    # Simplified SQL alchemy approach
    # Getting all products for term is easier
    products = db.query(Product).join(SearchConfig).filter(SearchConfig.term == term).limit(50).all()
    if not products: return 0.0
    return sum([p.price for p in products]) / len(products)

def scrape_and_save(db, config):
    results = scrape_vinted(config)
    new_count = 0
    fair_price = get_fair_price(db, config.term) if config.term else 0
    
    for item in results:
        existing = db.query(Product).filter_by(url=item['url']).first()
        
        # Download Image
        local_img = None
        if item.get('image_url'):
            # Fake ID generation for filename since we don't have DB ID yet
            temp_id = abs(hash(item['url'])) 
            local_img = download_image_as_avif(item['image_url'], temp_id)
            
        if not existing:
            new_product = Product(
                search_config_id=config.id,
                title=item.get('title'),
                brand=item.get('brand'),
                price=item.get('price'),
                size=item.get('size'),
                url=item.get('url'),
                image_url=item.get('image_url'),
                local_image_path=local_img
            )
            db.add(new_product)
            db.commit() # Commit to get ID
            
            # Price History
            ph = PriceHistory(product_id=new_product.id, price=item.get('price'))
            db.add(ph)
            
            # DEAL DETECTION & ALERT
            # Logic: If price < 70% of fair price AND fair_price > 0
            if fair_price > 0 and item.get('price') < (fair_price * 0.7):
                msg = f"🔥 *¡Oportunidad Detectada!*\n\nitem: {item.get('title')}\nPrecio: {item.get('price')}€ (Media: {fair_price:.2f}€)\nURL: {item.get('url')}"
                send_telegram_alert(msg)
                
            new_count += 1
        else:
            # Update price if changed
            if abs(existing.price - item.get('price')) > 0.5:
                existing.price = item.get('price')
                ph = PriceHistory(product_id=existing.id, price=item.get('price'))
                db.add(ph)
                
    config.last_run = datetime.utcnow()
    db.commit()
    return new_count

# --- UI ---

st.title("⚡ Vinted Intelligence System")

# Navigation
page = st.sidebar.radio("Navegación", ["📊 Dashboard", "🛠️ Configuración", "🔍 Logs"])

if page == "📊 Dashboard":
    with st.sidebar.expander("➕ Nueva Búsqueda", expanded=True):
        with st.form("add_search"):
            term = st.text_input("Palabras Clave (o Marca)", placeholder="Nike Jordan 1")
            
            # Advanced Filters
            c1, c2 = st.columns(2)
            brand_filter = c1.text_input("Marca (Específica)", placeholder="Jordan")
            cat_list = list(VINTED_CATALOG_IDS.keys())
            cats = c2.multiselect("Categoría", cat_list)
            
            c3, c4 = st.columns(2)
            min_p = c3.number_input("Min €", 0.0)
            max_p = c4.number_input("Max €", 0.0)
            
            col_list = list(VINTED_COLOR_IDS.keys())
            colors = st.multiselect("Colores", col_list)
            
            sizes = st.multiselect("Tallas", list(VINTED_SIZE_IDS.keys()))
            conds = st.multiselect("Estado", list(VINTED_CONDITION_IDS.keys()))
            
            if st.form_submit_button("Guardar Rastreador"):
                db = next(get_db())
                # Concatenate IDs
                cat_ids = ",".join(cats) if cats else None
                col_ids = ",".join(colors) if colors else None
                
                nc = SearchConfig(
                    term=term,
                    brand_name=brand_filter,
                    min_price=min_p, 
                    max_price=max_p if max_p > 0 else None,
                    sizes=",".join(sizes),
                    condition=",".join(conds),
                    color_ids=col_ids,
                    catalog_ids=cat_ids
                )
                db.add(nc)
                db.commit()
                st.success("Rastreador guardado.")
                db.close()
                st.rerun()

    # Active Trackers
    st.subheader("Rastreadores Activos")
    db = next(get_db())
    configs = db.query(SearchConfig).all()
    if configs:
        for c in configs:
            col1, col2, col3 = st.columns([6, 2, 2])
            with col1:
                st.markdown(f"**{c.term or c.brand_name}**")
                tags = []
                if c.min_price or c.max_price: tags.append(f"{c.min_price}-{c.max_price}€")
                if c.sizes: tags.append(f"Tallas: {c.sizes}")
                st.caption(", ".join(tags))
            with col2:
                if st.button("🔄 Escanear", key=f"s_{c.id}"):
                    n = scrape_and_save(db, c)
                    st.toast(f"{n} nuevos items")
            with col3:
                if st.button("🗑️", key=f"d_{c.id}"):
                    db.delete(c)
                    db.commit()
                    st.rerun()
    
    st.divider()
    
    # Results Table
    st.subheader("💎 Mercado en Tiempo Real")
    products = db.query(Product).order_by(Product.scanned_at.desc()).limit(100).all()
    
    if products:
        data = []
        for p in products:
            img_path = p.image_url # Default remote
            if p.local_image_path:
                # Streamlit serves static files if configured, but for simpler docker usage we keep remote for table usually
                # Or we can serve if we map static folder. For now, use remote for ease.
                pass
            
            data.append({
                "Foto": p.image_url,
                "Producto": p.title,
                "Precio": p.price,
                "Talla": p.size,
                "Marca": p.brand,
                "Detectado": p.scanned_at,
                "URL": p.url
            })
        
        df = pd.DataFrame(data)
        st.dataframe(
            df,
            column_config={
                "Foto": st.column_config.ImageColumn(width="small"),
                "URL": st.column_config.LinkColumn("Ir a Vinted"),
                "Precio": st.column_config.NumberColumn(format="%.2f €"),
                "Detectado": st.column_config.DatetimeColumn(format="HH:mm DD/MM")
            },
            hide_index=True,
            use_container_width=True
        )
    db.close()

elif page == "🛠️ Configuración":
    st.header("⚙️ Ajustes del Sistema")
    db = next(get_db())
    
    # Telegram
    st.subheader("🔔 Notificaciones Telegram")
    curr_token = db.query(Config).filter_by(key="telegram_token").first()
    curr_chat = db.query(Config).filter_by(key="telegram_chat_id").first()
    
    with st.form("tg_conf"):
        t_token = st.text_input("Bot Token", value=curr_token.value if curr_token else "")
        t_chat = st.text_input("Chat ID", value=curr_chat.value if curr_chat else "")
        
        if st.form_submit_button("Guardar Credenciales"):
            # Upsert
            def upsert(k, v):
                obj = db.query(Config).filter_by(key=k).first()
                if not obj: db.add(Config(key=k, value=v))
                else: obj.value = v
            
            upsert("telegram_token", t_token)
            upsert("telegram_chat_id", t_chat)
            db.commit()
            st.success("Guardado.")
            
    if st.button("🔔 Probar Notificación"):
        send_telegram_alert("✅ Prueba de configuración exitosa.")
        st.toast("Mensaje de prueba enviado.")

    st.divider()
    
    # Scheduler
    st.subheader("⏱️ Frecuencia de Escaneo")
    sch_active = db.query(Config).filter_by(key="scheduler_active").first()
    sch_int = db.query(Config).filter_by(key="scheduler_interval").first()
    
    act = st.toggle("Scraper Automático Activo", value=(sch_active.value == "1") if sch_active else True)
    inte = st.slider("Intervalo (Horas)", 1, 48, int(sch_int.value) if sch_int else 6)
    
    if st.button("Actualizar Tareas"):
        def upsert(k, v):
            obj = db.query(Config).filter_by(key=k).first()
            if not obj: db.add(Config(key=k, value=v))
            else: obj.value = v
        
        upsert("scheduler_active", "1" if act else "0")
        upsert("scheduler_interval", str(inte))
        db.commit()
        
        update_scheduler_job(db)
        st.success("Planificador actualizado.")
        
    db.close()

elif page == "🔍 Logs":
    st.header("Registro del Sistema")
    if st.button("Refrescar"): st.rerun()
    
    db = next(get_db())
    logs = db.query(ScraperLog).order_by(ScraperLog.timestamp.desc()).limit(100).all()
    for l in logs:
        c = "red" if l.level == "ERROR" else "orange" if l.level == "WARNING" else "green"
        st.markdown(f":{c}[[{l.timestamp.strftime('%H:%M:%S')}]] **{l.level}**: {l.message}")
    db.close()
