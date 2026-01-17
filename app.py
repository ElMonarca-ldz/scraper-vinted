import streamlit as st
import pandas as pd
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
import threading
import logging

from database import get_db, init_db, SearchConfig, Product, engine, ScraperLog
from scraper import scrape_vinted, VINTED_SIZE_IDS, VINTED_CONDITION_IDS

# --- CONFIGURATION ---
st.set_page_config(page_title="Scraper Vinted", layout="wide", page_icon="🛍️")

# Initialize DB
init_db()

# --- SCHEDULER SETUP ---
# Ensure scheduler runs once and persists across Streamlit re-runs
if 'scheduler' not in st.session_state:
    st.session_state.scheduler = BackgroundScheduler()
    st.session_state.scheduler.start()
    logging.info("Scheduler started.")

def run_scheduled_scans():
    """Function to be called by the scheduler to scan all active configs."""
    logging.info("Starting scheduled daily scan...")
    db = next(get_db())
    configs = db.query(SearchConfig).all()
    for config in configs:
        logging.info(f"Scanning for: {config.term}")
        results = scrape_vinted(config)
        save_results(db, config.id, results)
    db.close()
    logging.info("Scheduled scan completed.")

# Add job if not exists (Basic check, effectively runs once per server start logic ideally)
if not st.session_state.scheduler.get_jobs():
    st.session_state.scheduler.add_job(run_scheduled_scans, 'interval', hours=24, id='daily_scan')

# --- HELPER FUNCTIONS ---

def save_results(db: Session, config_id: int, results: list):
    count = 0
    for item in results:
        # Check if URL exists to avoid duplicates
        existing = db.query(Product).filter_by(url=item['url']).first()
        if not existing:
            new_product = Product(
                search_config_id=config_id,
                title=item.get('title'),
                brand=item.get('brand'),
                price=item.get('price'),
                size=item.get('size'),
                url=item.get('url'),
                image_url=item.get('image_url')
            )
            db.add(new_product)
            count += 1
    
    # Update last run time
    config = db.query(SearchConfig).filter_by(id=config_id).first()
    config.last_run = datetime.utcnow()
    
    db.commit()
    return count

def highlight_deals(row, avg_prices):
    """
    Pandas styler function.
    Highlights row green if price is < 20% below average for that term.
    """
    term = row['Término']
    price = row['Precio']
    if term in avg_prices and avg_prices[term] > 0:
        if price < (avg_prices[term] * 0.8):
            return ['background-color: #d4edda'] * len(row)
    return [''] * len(row)

# --- UI LAYOUT ---

st.title("🛍️ Automatizador Vinted")

# Sidebar: Configuration
with st.sidebar:
    st.header("Configuración de Búsqueda")
    
    with st.expander("Agregar Nueva Búsqueda"):
        with st.form("new_search_form"):
            term = st.text_input("Término de Búsqueda", placeholder="ej. Nike Vintage Hoodie")
            c1, c2 = st.columns(2)
            min_p = c1.number_input("Precio Mín (€)", min_value=0.0, value=0.0, step=1.0)
            max_p = c2.number_input("Precio Máx (€)", min_value=0.0, value=100.0, step=1.0)
            
            # Size Multi-Select
            size_options = list(VINTED_SIZE_IDS.keys())
            selected_sizes = st.multiselect("Tallas", size_options)
            
            # Condition Multi-Select
            available_conditions = list(VINTED_CONDITION_IDS.keys())
            selected_conditions = st.multiselect("Estado", available_conditions)
            
            submitted = st.form_submit_button("Agregar Búsqueda")
            if submitted and term:
                db = next(get_db())
                # Limit to 15 configs
                if db.query(SearchConfig).count() >= 15:
                    st.error("Límite de 15 configuraciones alcanzado.")
                else:
                    new_config = SearchConfig(
                        term=term,
                        min_price=min_p,
                        max_price=max_p if max_p > 0 else None,
                        sizes=",".join(selected_sizes),
                        condition=",".join(selected_conditions)
                    )
                    db.add(new_config)
                    db.commit()
                    st.success(f"Agregado: {term}")
                db.close()

    st.divider()
    st.subheader("Búsquedas Activas")
    db = next(get_db())
    configs = db.query(SearchConfig).all()
    
    for c in configs:
        c1, c2 = st.columns([4, 1])
        c1.text(f"{c.term} ({c.min_price}-{c.max_price}€)\n[{c.sizes}]")
        if c2.button("❌", key=f"del_{c.id}"):
            db.delete(c)
            db.commit()
            st.rerun()
    db.close()

# Main Area: Tabs
tab1, tab2 = st.tabs(["📊 Panel de Control", "📜 Registro de Actividad"])

with tab1:
    # 1. Manual Execution
    st.subheader("🚀 Ejecución Manual")
    db = next(get_db())
    active_configs = db.query(SearchConfig).all()
    config_map = {c.term: c.id for c in active_configs}
    selected_terms = st.multiselect("Selecciona términos para escanear ahora:", list(config_map.keys()))
    
    if st.button("Iniciar Scraping"):
        if not selected_terms:
            st.warning("Por favor selecciona al menos un término.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for idx, term in enumerate(selected_terms):
                cid = config_map[term]
                target_config = db.query(SearchConfig).get(cid)
                status_text.text(f"Escaneando: {term}...")
                
                try:
                    # Scrape
                    results = scrape_vinted(target_config)
                    # Save
                    new_items = save_results(db, cid, results)
                    st.toast(f"{term}: {len(results)} items encontrados, {new_items} nuevos.", icon="✅")
                except Exception as e:
                    st.error(f"Error escaneando {term}: {e}")
                
                progress_bar.progress((idx + 1) / len(selected_terms))
                
            status_text.text("¡Listo!")
            st.success("Escaneo manual completado.")
            st.rerun() # Refresh to show new data
    
    # 2. Results Dashboard
    st.divider()
    st.subheader("🔎 Últimos Hallazgos")
    
    # Fetch all products
    products = db.query(Product).join(SearchConfig).all()
    if products:
        data = []
        for p in products:
            data.append({
                "Fecha": p.scanned_at,
                "Término": p.search_config.term,
                "Título": p.title,
                "Marca": p.brand,
                "Talla": p.size,
                "Precio": p.price,
                "Enlace": p.url,
                "Imagen": p.image_url
            })
        
        df = pd.DataFrame(data)
        
        # Calculate averages for highlighting
        avg_prices = df.groupby('Término')['Precio'].mean().to_dict()
        
        # Filters for the table
        col1, col2 = st.columns(2)
        filter_term = col1.selectbox("Filtrar por término", ["Todos"] + list(df['Término'].unique()))
        
        if filter_term != "Todos":
            df = df[df['Término'] == filter_term]
        
        # Formatting
        st.markdown(f"**Total Items:** {len(df)}")
        
        # Apply Highlight
        styled_df = df.style.apply(lambda x: highlight_deals(x, avg_prices), axis=1)
        
        st.dataframe(
            styled_df,
            column_config={
                "Enlace": st.column_config.LinkColumn("Ver en Vinted", display_text="Abrir"),
                "Imagen": st.column_config.ImageColumn("Foto"),
                "Precio": st.column_config.NumberColumn("Precio", format="%.2f €"),
                "Fecha": st.column_config.DatetimeColumn("Encontrado", format="D MMM, HH:mm"),
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Aún no hay datos. Agrega una búsqueda y ejecuta el scraper.")

with tab2:
    st.subheader("Registro en Tiempo Real")
    st.markdown("Aquí puedes ver qué está haciendo el scraper paso a paso.")
    
    if st.button("Actualizar Registros"):
        st.rerun()
        
    db = next(get_db())
    # Get last 50 logs
    logs = db.query(ScraperLog).order_by(ScraperLog.timestamp.desc()).limit(50).all()
    
    for log in logs:
        color = "black"
        if log.level == "ERROR":
            color = "red"
        elif log.level == "WARNING":
            color = "orange"
        else:
            color = "green"
            
        st.markdown(f"<span style='color:{color}'>**[{log.timestamp.strftime('%H:%M:%S')}]** {log.message}</span>", unsafe_allow_html=True)
    
    db.close()

db.close()
