import logging
import time
import random
from playwright.sync_api import sync_playwright

# --- CONFIGURATION & CONSTANTS ---
from database import SessionLocal, ScraperLog
from datetime import datetime

# Logging setup - also log to DB
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def log_to_db(message, level="INFO"):
    """Writes log message to SQLite so Streamlit can read it."""
    logging.info(message) # Keep console logging for Docker logs
    try:
        db = SessionLocal()
        log_entry = ScraperLog(level=level, message=message, timestamp=datetime.utcnow())
        db.add(log_entry)
        db.commit()
        db.close()
    except Exception as e:
        print(f"Failed to log to DB: {e}")

# Extended Size IDs
VINTED_SIZE_IDS = {
    # Clothing
    "XS": "206", "S": "207", "M": "208", "L": "209", "XL": "210", "XXL": "211", "3XL": "212",
    # Shoes (Expanded)
    "36": "770", "37": "771", "38": "772", "39": "773", "40": "774",
    "41": "775", "42": "776", "43": "777", "44": "778", "45": "779", 
    "46": "780", "47": "781", "48": "782", "49": "783", "50": "784" # Assumed IDs based on pattern, user requested 46+
}

VINTED_CONDITION_IDS = {
    "Nuevo con etiquetas": "6",
    "Nuevo sin etiquetas": "104",
    "Muy bueno": "2",
    "Bueno": "3",
    "Satisfactorio": "4"
}

BASE_URL = "https://www.vinted.es/vetements" 

def build_search_url(term, min_price=None, max_price=None, sizes=None, conditions=None):
    """
    Constructs the Vinted search URL based on parameters.
    """
    base = "https://www.vinted.es/catalog"
    query_params = []
    
    # Text search
    if term:
        query_params.append(f"search_text={term.replace(' ', '+')}")
    
    # Price filters
    if min_price is not None:
        query_params.append(f"price_from={min_price}")
    if max_price is not None:
        query_params.append(f"price_to={max_price}")
        
    # Size filters
    if sizes:
        for size in sizes:
            if size in VINTED_SIZE_IDS:
                query_params.append(f"size_ids[]={VINTED_SIZE_IDS[size]}")
                
    # Condition filters
    if conditions:
        for cond in conditions:
            # Check if cond is name or ID
            if cond in VINTED_CONDITION_IDS:
                query_params.append(f"status_ids[]={VINTED_CONDITION_IDS[cond]}")
            elif cond in VINTED_CONDITION_IDS.values():
                query_params.append(f"status_ids[]={cond}")
    
    query_params.append("order=newest_first")
    
    url = f"{base}?{'&'.join(query_params)}"
    return url

def scrape_vinted(search_config):
    """
    Scrapes Vinted for a given SearchConfig object.
    """
    results = []
    
    term = search_config.term
    log_to_db(f"Iniciando búsqueda para: {term}")

    min_price = search_config.min_price
    max_price = search_config.max_price
    
    # Parse sizes
    sizes_list = []
    if search_config.sizes:
        sizes_list = [s.strip() for s in search_config.sizes.split(',')]
        
    # Parse conditions
    conditions_list = []
    if hasattr(search_config, 'condition') and search_config.condition:
        conditions_list = [c.strip() for c in search_config.condition.split(',')]
        
    search_url = build_search_url(term, min_price, max_price, sizes_list, conditions_list)
    log_to_db(f"URL generada: {search_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        
        page = context.new_page()
        
        try:
            log_to_db("Navegando a Vinted...", "INFO")
            page.goto(search_url, timeout=60000)
            
            # Cookie banner
            try:
                button = page.query_selector('button[id*="onetrust-accept-btn-handler"]') 
                if button:
                    button.click()
            except Exception:
                pass 

            time.sleep(3) 
            
            # Wait for items
            try:
                page.wait_for_selector('div[data-testid="grid-item"]', timeout=15000)
                items = page.query_selector_all('div[data-testid="grid-item"]')
                log_to_db(f"Se encontraron {len(items)} artículos en la página.", "INFO")
                
                for item in items[:20]: 
                    try:
                        product_data = {}
                        
                        link_elem = item.query_selector('a')
                        if not link_elem:
                            continue
                            
                        url_suffix = link_elem.get_attribute('href')
                        product_data['url'] = url_suffix
                        if not product_data['url'].startswith('http'):
                            product_data['url'] = f"https{product_data['url']}" if product_data['url'].startswith('://') else f"https://www.vinted.es{product_data['url']}"
                            
                        product_data['title'] = link_elem.get_attribute('title') or "Sin título"
                        
                        # Price
                        price_elem = item.query_selector('p[class*="web_ui__Text__title"]')
                        if price_elem:
                            raw_price = price_elem.inner_text()
                            clean_price = raw_price.replace('€', '').replace(' ', '').replace(',', '.')
                            try:
                                product_data['price'] = float(clean_price)
                            except:
                                product_data['price'] = 0.0
                        else:
                            product_data['price'] = 0.0

                        # Meta (Brand, Size)
                        meta_texts = item.query_selector_all('p[class*="web_ui__Text__muted"]')
                        if len(meta_texts) >= 2:
                            product_data['size'] = meta_texts[0].inner_text()
                            product_data['brand'] = meta_texts[1].inner_text()
                        elif len(meta_texts) == 1:
                            product_data['brand'] = meta_texts[0].inner_text()
                            product_data['size'] = "N/A"
                        else:
                            product_data['brand'] = "Desconocida"
                            product_data['size'] = "N/A"

                        # Image
                        img_elem = item.query_selector('img')
                        if img_elem:
                             product_data['image_url'] = img_elem.get_attribute('src')

                        results.append(product_data)
                        
                    except Exception as e:
                        continue
            except Exception as e:
                 log_to_db(f"No se encontraron items o cambió el selector: {e}", "WARNING")
                    
        except Exception as e:
            log_to_db(f"Error crítico scraping {term}: {e}", "ERROR")
            
        browser.close()
        
    log_to_db(f"Búsqueda finalizada. {len(results)} items procesados.", "INFO")
    return results

if __name__ == "__main__":
    # Test function
    class MockConfig:
        term = "nike vintage"
        min_price = 10
        max_price = 50
        sizes = "L,XL"
        
    print(scrape_vinted(MockConfig()))
