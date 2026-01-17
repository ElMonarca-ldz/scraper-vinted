import streamlit as st
import pandas as pd
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
import threading
import logging

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
import logging
import os
import sys
import asyncio
from io import BytesIO

# Fix for Windows asyncio loop (NotImplementedError in Playwright)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from database import get_db, init_db, SearchConfig, Product, ScraperLog, Config, PriceHistory, Brand, AlertRule
from scraper import scrape_vinted, VINTED_SIZE_IDS, VINTED_CONDITION_IDS, VINTED_COLOR_IDS, VINTED_CATALOG_IDS, send_telegram_alert, download_image_as_avif, verify_sold_status, fetch_vinted_brands

# --- CONFIGURATION ---
st.set_page_config(page_title="Vinted Pro Analytics", layout="wide", page_icon="📈")

# Initialize DB
init_db()

# --- SCHEDULER SETUP ---
if 'scheduler' not in st.session_state:
    st.session_state.scheduler = BackgroundScheduler()
    st.session_state.scheduler.start()
    logging.info("Scheduler started.")

def update_scheduler_job(db: Session):
    scheduler = st.session_state.scheduler
    if scheduler.get_job('main_scan'): scheduler.remove_job('main_scan')
    if scheduler.get_job('sold_check'): scheduler.remove_job('sold_check')
        
    active_setting = db.query(Config).filter_by(key="scheduler_active").first()
    interval_setting = db.query(Config).filter_by(key="scheduler_interval").first()
    is_active = active_setting.value == "1" if active_setting else True
    interval = int(interval_setting.value) if interval_setting else 6
    
    if is_active:
        scheduler.add_job(run_scheduled_scans, 'interval', hours=interval, id='main_scan')
        scheduler.add_job(run_sold_check_job, 'interval', hours=24, id='sold_check')

def run_scheduled_scans():
    db = next(get_db())
    configs = db.query(SearchConfig).all()
    for config in configs:
        scrape_and_save(db, config)
    db.close()

def run_sold_check_job():
    db = next(get_db())
    products = db.query(Product).filter(Product.is_sold == 0).order_by(Product.scanned_at.desc()).limit(100).all()
    for p in products:
        status = verify_sold_status(p.url)
        if status == 'sold':
            p.is_sold = 1
            p.sold_at = datetime.utcnow()
        elif status == 'deleted':
            p.is_sold = 1
    db.commit()
    db.close()

if not st.session_state.scheduler.get_jobs():
    st.session_state.scheduler.add_job(run_scheduled_scans, 'interval', hours=6, id='main_scan')
    st.session_state.scheduler.add_job(run_sold_check_job, 'interval', hours=24, id='sold_check')
    
# --- ANALYTICS ENGINE ---

def calculate_stats(prices):
    if not prices: return 0, 0
    arr = np.array(prices)
    return np.mean(arr), np.std(arr)

def check_global_alerts(db, product):
    """
    Checks if a product matches any active AlertRule.
    """
    rules = db.query(AlertRule).filter_by(is_active=1).all()
    for rule in rules:
        # Check Brand
        if rule.brand_list:
            if not product.brand or product.brand.lower() not in [b.lower().strip() for b in rule.brand_list.split(',')]:
                continue
        
        # Check Price
        if rule.max_price is not None and product.price > rule.max_price:
            continue
            
        # Check Z-Score (Complex)
        if rule.min_z_score is not None:
             # Need context stats
             ph = db.query(PriceHistory).filter(PriceHistory.product_id == product.id).all() # This is only for this product
             # We need stats for the SEARCH TERM usually
             # Approximate with current price vs search config average?
             # Let's use the scrape_and_save pre-calculated stats for now or skip if complex
             pass 

        # If we get here, Match!
        send_telegram_alert(f"🚨 **ALERTA: {rule.name}**\n\n{product.title}\n{product.price}€\nURL: {product.url}")

def scrape_and_save(db, config):
    results = scrape_vinted(config)
    new_count = 0
    
    # Get stats for Z-Score
    # We look at all products for this search config to build a baseline
    all_prices = [p.price for p in config.products]
    hist_mean, hist_std = calculate_stats(all_prices)
    
    for item in results:
        existing = db.query(Product).filter_by(url=item['url']).first()
        
        # Image
        local_img = None
        if item.get('image_url'):
            temp_id = abs(hash(item['url'])) 
            local_img = download_image_as_avif(item['image_url'], temp_id)
            
        p_obj = existing
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
            db.commit()
            p_obj = new_product
            
            # History
            db.add(PriceHistory(product_id=p_obj.id, price=item.get('price')))
            new_count += 1
            
            # CHECK ALERTS
            check_global_alerts(db, p_obj)
            
            # AUTO-Z-SCORE ALERT (Legacy)
            if hist_mean > 0:
                 z_score = (item.get('price') - hist_mean) / (hist_std if hist_std > 0 else 1)
                 if z_score < -1.5: # 1.5 Sigma event
                     send_telegram_alert(f"📉 **Oportunidad Estadística (Z={z_score:.1f})**\n\n{item.get('title')}\n{item.get('price')}€ (Avg: {hist_mean:.1f}€)")

        else:
            # Price Update
            if abs(existing.price - item.get('price')) > 0.5:
                existing.price = item.get('price')
                db.add(PriceHistory(product_id=existing.id, price=item.get('price')))
                
    config.last_run = datetime.utcnow()
    db.commit()
    return new_count

# --- UI ---

# Sidebar Navigation
mode = st.sidebar.radio("Menú", ["📊 Dashboard", "📈 Análisis de Mercado", "🚨 Reglas y Alertas", "🛠️ Configuración", "🔍 Logs"])

if mode == "📊 Dashboard":
    st.header("Monitor de Oportunidades")
    
    # Add Search
    with st.expander("➕ Nueva Búsqueda", expanded=True):
        with st.form("new_search"):
            st.info("Configura los parámetros para que el bot rastree Vinted.")
            term = st.text_input("Término", help="Lo que escribirías en el buscador de Vinted")
            
            db = next(get_db())
            # Brand Selector: Dynamic or Text
            brands_db = db.query(Brand).order_by(Brand.title).all()
            if brands_db:
                brand_mode = st.radio("Selección de Marca", ["Escribir Manual", "Seleccionar de Lista"], horizontal=True)
                if brand_mode == "Seleccionar de Lista":
                    b_obj = st.selectbox("Marca", [b.title for b in brands_db])
                    brand_val = b_obj
                else:
                    brand_val = st.text_input("Marca (Manual)")
            else:
                brand_val = st.text_input("Marca (Manual)", help="Sincroniza marcas en Configuración para ver una lista aquí.")
            
            c1, c2 = st.columns(2)
            min_p = c1.number_input("Min €", 0.0)
            max_p = c2.number_input("Max €", 0.0)
            
            # Limits
            c3, c4 = st.columns(2)
            lim_pages = c3.number_input("Máx Páginas", 1, 50, 5, help="Cuántas páginas de Vinted recorrer.")
            lim_items = c4.number_input("Máx Items", 10, 1000, 100, help="Detener tras encontrar N items.")
            
            if st.form_submit_button("Guardar"):
                nc = SearchConfig(
                    term=term, 
                    brand_name=brand_val, 
                    min_price=min_p, 
                    max_price=max_p if max_p > 0 else None,
                    max_pages=lim_pages,
                    max_items=lim_items
                )
                db.add(nc)
                db.commit()
                st.success("Guardado")
                st.rerun()
            db.close()
            
    # Active
    st.subheader("Rastreadores")
    db = next(get_db())
    configs = db.query(SearchConfig).all()
    for c in configs:
        with st.container(border=True):
            cols = st.columns([5, 2, 1])
            cols[0].markdown(f"**{c.term}** - {c.brand_name or 'Cualquier marca'} | 📄 {c.max_pages} pgs")
            if cols[1].button("Escanear", key=f"s_{c.id}"):
                with st.status(f"Escaneando {c.term}...", expanded=True) as status:
                    status.write("Iniciando navegador...")
                    n = scrape_and_save(db, c)
                    status.update(label=f"Completado: {n} nuevos.", state="complete")
            if cols[2].button("🗑️", key=f"d_{c.id}"):
                db.delete(c)
                db.commit()
                st.rerun()
    
    # Results
    st.divider()
    st.subheader("Últimos Hallazgos")
    
    # BATCH DELETE FUNCTION
    with st.expander("🗑️ Gestión de Lotes (Borrado Masivo)"):
        # Group products by scanned_at (minute precision)
        # SQLite dialect for date truncation equivalent
        # For simplicity, we fetch distinct scanned_at and count
        dates = db.query(Product.scanned_at).distinct().order_by(Product.scanned_at.desc()).limit(20).all()
        # Flatten
        unique_dates = sorted(list(set([d[0].strftime("%Y-%m-%d %H:%M") for d in dates])), reverse=True)
        
        target_batch = st.selectbox("Seleccionar Lote (Fecha/Hora)", unique_dates, index=None)
        if target_batch and st.button(f"Eliminar items de {target_batch}"):
            # Parse back
            # Deleting by string match on strftime is tricky in SQL directly without specific func
            # Let's do a range check (Minute start to Minute end)
            dt_start = datetime.strptime(target_batch, "%Y-%m-%d %H:%M")
            dt_end = dt_start + timedelta(minutes=1)
            
            deleted = db.query(Product).filter(Product.scanned_at >= dt_start, Product.scanned_at < dt_end).delete()
            db.commit()
            st.success(f"Eliminados {deleted} productos del lote {target_batch}.")
            st.rerun()

    prods = db.query(Product).order_by(Product.scanned_at.desc()).limit(150).all()
    if prods:
        # Prepare for DataFrame
        data = []
        for p in prods:
             data.append({
                 "Img": p.image_url, 
                 "Producto": p.title, 
                 "Precio": f"{p.price} €", 
                 "Marca": p.brand, 
                 "URL": p.url,
                 "Estado": "🔴 Vendido" if p.is_sold else "🟢 Disp."
             })
        st.dataframe(pd.DataFrame(data), column_config={"Img": st.column_config.ImageColumn(), "URL": st.column_config.LinkColumn()}, hide_index=True)
    
    db.close()

elif mode == "📈 Análisis de Mercado":
    st.title("Inteligencia de Precios")
    st.info("Analiza la evolución de precios y la distribución del mercado.")
    
    db = next(get_db())
    # Load all price history
    history = pd.read_sql(db.query(PriceHistory).statement, db.bind)
    products = pd.read_sql(db.query(Product).statement, db.bind)
    
    if not history.empty:
        full_df = pd.merge(history, products, left_on="product_id", right_on="id", suffixes=('_hist', '_prod'))
        
        # FILTERS
        st.subheader("Filtros")
        f_c1, f_c2 = st.columns(2)
        all_brands = sorted(full_df['brand'].astype(str).unique())
        sel_brand = f_c1.multiselect("Filtrar Marca", all_brands)
        sel_term = f_c2.text_input("Filtrar en Título")
        
        filtered_df = full_df.copy()
        if sel_brand:
            filtered_df = filtered_df[filtered_df['brand'].isin(sel_brand)]
        if sel_term:
            filtered_df = filtered_df[filtered_df['title'].str.contains(sel_term, case=False, na=False)]
            
        # 1. Price Matrix
        st.subheader(f"Matriz de Evolución ({len(filtered_df)} registros)")
        pivot = filtered_df.pivot_table(index='title', columns=pd.to_datetime(filtered_df['timestamp']).dt.date, values='price_hist', aggfunc='last')
        st.dataframe(pivot)
        
        # Export
        if st.button("Descargar Excel"):
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                pivot.to_excel(writer, sheet_name='Matriz')
                filtered_df.to_excel(writer, sheet_name='RawData')
            st.download_button("📥 Descargar .xlsx", output.getvalue(), "vinted_analisis.xlsx")
            
        # 2. Stats
        st.subheader("Estadísticas de Mercado (Box Plot)")
        if 'brand' in filtered_df.columns:
            st.bar_chart(filtered_df.groupby('brand')['price_prod'].mean())
            
    else:
        st.warning("No hay suficiente historial de precios.")
    db.close()

elif mode == "🚨 Reglas y Alertas":
    st.header("Motor de Alertas")
    st.info("Define reglas globales. Si un escaneo coincide, te llegará un mensaje.")
    
    with st.form("new_rule"):
        name = st.text_input("Nombre de la Regla", placeholder="Ej: Gangas Nike")
        brands = st.text_input("Marcas (separadas por coma)", placeholder="Nike, Adidas")
        max_p = st.number_input("Precio Máximo", 0.0)
        
        if st.form_submit_button("Crear Regla"):
            db = next(get_db())
            db.add(AlertRule(name=name, brand_list=brands, max_price=max_p if max_p > 0 else None))
            db.commit()
            st.success("Regla creada.")
            db.close()
            
    # List Rules
    db = next(get_db())
    rules = db.query(AlertRule).all()
    for r in rules:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{r.name}** | Marcas: {r.brand_list} | Max: {r.max_price}€")
            if c2.button("Borrar", key=f"rd_{r.id}"):
                db.delete(r)
                db.commit()
                st.rerun()
    db.close()

elif mode == "🛠️ Configuración":
    st.header("Configuración")
    
    # Telegram
    with st.expander("🔔 Telegram", expanded=True):
        db = next(get_db())
        current_token = db.query(Config).filter_by(key="telegram_token").first()
        current_chat = db.query(Config).filter_by(key="telegram_chat_id").first()
        
        with st.form("tg"):
            tk = st.text_input("Token", value=current_token.value if current_token else "")
            cid = st.text_input("Chat ID", value=current_chat.value if current_chat else "")
            if st.form_submit_button("Guardar"):
                # Upsert logic simplified
                if not current_token: db.add(Config(key="telegram_token", value=tk))
                else: current_token.value = tk
                if not current_chat: db.add(Config(key="telegram_chat_id", value=cid))
                else: current_chat.value = cid
                db.commit()
                st.toast("Guardado")
        db.close()

    # Brand Sync
    with st.expander("🏷️ Marcas Vinted"):
        st.markdown("Sincroniza marcas populares para tener el autocompletado.")
        keyword = st.text_input("Buscar marca para importar ID (ej: Nike)", value="Nike")
        if st.button("Buscar e Importar"):
            db = next(get_db())
            found = fetch_vinted_brands(keyword)
            count = 0
            for b_data in found:
                exists = db.query(Brand).filter_by(vinted_id=b_data['id']).first()
                if not exists:
                    db.add(Brand(vinted_id=b_data['id'], title=b_data['title']))
                    count += 1
            db.commit()
            st.success(f"Importadas {count} marcas nuevas.")
            db.close()

elif mode == "🔍 Logs":
    st.header("Logs en Vivo")
    if st.button("Refresh"): st.rerun()
    db = next(get_db())
    logs = db.query(ScraperLog).order_by(ScraperLog.timestamp.desc()).limit(50).all()
    for l in logs:
        st.caption(f"{l.timestamp.strftime('%H:%M:%S')} - {l.message}")
    db.close()
