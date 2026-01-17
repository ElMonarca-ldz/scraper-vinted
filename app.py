import streamlit as st
import pandas as pd
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
import threading
import logging

from database import get_db, init_db, SearchConfig, Product, engine
from scraper import scrape_vinted, VINTED_SIZE_IDS

# --- CONFIGURATION ---
st.set_page_config(page_title="Vinted Scraper", layout="wide", page_icon="🛍️")

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
    # row is a Series. We need the term average.
    # This function applies style to the whole row but depends on external 'avg_prices' dict.
    # Since we can't easily pass the dict into the styler function directly in a vectorized way for all rows if terms differ,
    # we'll pre-calculate a 'deal' boolean column or do it per cell.
    
    # Simpler approach: return styles list
    term = row['Term']
    price = row['Price']
    if term in avg_prices and avg_prices[term] > 0:
        if price < (avg_prices[term] * 0.8):
            return ['background-color: #d4edda'] * len(row)
    return [''] * len(row)

# --- UI LAYOUT ---

st.title("🛍️ Vinted Automator")

# Sidebar: Configuration
with st.sidebar:
    st.header("Search Configuration")
    
    with st.expander("Add New Search"):
        with st.form("new_search_form"):
            term = st.text_input("Search Term", placeholder="e.g. Nike Vintage Hoodie")
            c1, c2 = st.columns(2)
            min_p = c1.number_input("Min Price (€)", min_value=0.0, value=0.0, step=1.0)
            max_p = c2.number_input("Max Price (€)", min_value=0.0, value=100.0, step=1.0)
            
            # Size Multi-Select
            size_options = list(VINTED_SIZE_IDS.keys())
            selected_sizes = st.multiselect("Sizes", size_options)
            
            submitted = st.form_submit_button("Add Search")
            if submitted and term:
                db = next(get_db())
                # Limit to 15 configs
                if db.query(SearchConfig).count() >= 15:
                    st.error("Limit of 15 search configurations reached.")
                else:
                    new_config = SearchConfig(
                        term=term,
                        min_price=min_p,
                        max_price=max_p if max_p > 0 else None,
                        sizes=",".join(selected_sizes)
                    )
                    db.add(new_config)
                    db.commit()
                    st.success(f"Added {term}")
                db.close()

    st.divider()
    st.subheader("Existing Searches")
    db = next(get_db())
    configs = db.query(SearchConfig).all()
    
    for c in configs:
        c1, c2 = st.columns([4, 1])
        c1.text(f"{c.term} ({c.min_price}-{c.max_price}€) [{c.sizes}]")
        if c2.button("❌", key=f"del_{c.id}"):
            db.delete(c)
            db.commit()
            st.rerun()
    db.close()

# Main Area: Control & Results

# 1. Manual Execution
st.subheader("🚀 Manual Execution")
db = next(get_db())
active_configs = db.query(SearchConfig).all()
config_map = {c.term: c.id for c in active_configs}
selected_terms = st.multiselect("Select terms to scan now:", list(config_map.keys()))

if st.button("Start Scraping"):
    if not selected_terms:
        st.warning("Please select at least one term.")
    else:
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for idx, term in enumerate(selected_terms):
            cid = config_map[term]
            target_config = db.query(SearchConfig).get(cid)
            status_text.text(f"Scraping: {term}...")
            
            try:
                # Scrape
                results = scrape_vinted(target_config)
                # Save
                new_items = save_results(db, cid, results)
                st.toast(f"{term}: Found {len(results)} items, {new_items} new.", icon="✅")
            except Exception as e:
                st.error(f"Error scraping {term}: {e}")
            
            progress_bar.progress((idx + 1) / len(selected_terms))
            
        status_text.text("Done!")
        st.success("Manual scan completed.")
        st.rerun() # Refresh to show new data

# 2. Results Dashboard
st.divider()
st.subheader("📊 Latest Findings")

# Fetch all products
products = db.query(Product).join(SearchConfig).all()
if products:
    data = []
    for p in products:
        data.append({
            "Scan Date": p.scanned_at,
            "Term": p.search_config.term,
            "Title": p.title,
            "Brand": p.brand,
            "Size": p.size,
            "Price": p.price,
            "Link": p.url,
            "Image": p.image_url
        })
    
    df = pd.DataFrame(data)
    
    # Calculate averages for highlighting
    avg_prices = df.groupby('Term')['Price'].mean().to_dict()
    
    # Filters for the table
    col1, col2 = st.columns(2)
    filter_term = col1.selectbox("Filter by Term", ["All"] + list(df['Term'].unique()))
    
    if filter_term != "All":
        df = df[df['Term'] == filter_term]
    
    # Formatting
    st.markdown(f"**Total Items:** {len(df)}")
    
    # Apply Highlight
    # We can't use style.apply with st.dataframe for complex row logic easily if we want interactive sorting
    # But st.dataframe supports 'style' object.
    
    styled_df = df.style.apply(lambda x: highlight_deals(x, avg_prices), axis=1)
    
    st.dataframe(
        styled_df,
        column_config={
            "Link": st.column_config.LinkColumn("Product Link"),
            "Image": st.column_config.ImageColumn("Preview"),
            "Price": st.column_config.NumberColumn("Price", format="%.2f €"),
            "Scan Date": st.column_config.DatetimeColumn("Found At", format="D MMM, HH:mm"),
        },
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("No data found yet. Add a search and run the scraper.")

db.close()
