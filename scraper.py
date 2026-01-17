import logging
import time
import random
import os
import requests
from io import BytesIO
import PIL.Image
import pillow_avif
from playwright.sync_api import sync_playwright
from datetime import datetime

# --- CONFIGURATION & CONSTANTS ---
from database import SessionLocal, ScraperLog, Config

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

# --- TELEGRAM NOTIFIER ---
def send_telegram_alert(message):
    try:
        db = SessionLocal()
        token = db.query(Config).filter_by(key="telegram_token").first()
        chat_id = db.query(Config).filter_by(key="telegram_chat_id").first()
        db.close()
        
        if token and chat_id:
            url = f"https://api.telegram.org/bot{token.value}/sendMessage"
            payload = {
                "chat_id": chat_id.value,
                "text": message,
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log_to_db(f"Error enviando Telegram: {e}", "WARNING")

# --- IMAGE PROCESSING ---
def download_image_as_avif(image_url, product_id):
    """
    Downloads image, converts to AVIF, saves to /app/data/images/{id}.avif
    Returns relative path.
    """
    try:
        if not image_url: return None
        
        # Ensure dir exists
        save_dir = "/app/data/images"
        if not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)
            
        filename = f"{product_id}.avif"
        filepath = os.path.join(save_dir, filename)
        
        # Download
        response = requests.get(image_url, timeout=10)
        if response.status_code == 200:
            img = PIL.Image.open(BytesIO(response.content))
            # Convert
            img.save(filepath, "AVIF", quality=50) # Aggressive compression
            return filename
    except Exception as e:
        log_to_db(f"Error procesando imagen {image_url}: {e}", "WARNING")
        return None
    return None

# --- CONSTANTS (EXTENDED with real IDs or search logic) ---
# Note: Full ID list is massive. We implement key ones and enable text fallback.
# Source: Mapped from user request and common Vinted IDs.

VINTED_CATALOG_IDS = {
    # Women
    "Mujer": "1904",
    "Ropa": "4",
    "Zapatos": "16",
    # Men
    "Hombre": "5",
    "Ropa (H)": "78",
    "Zapatos (H)": "1231",
    # Specifics
    "Zapatillas": "1242",
    "Camisetas": "1994",
    "Sudaderas": "2013"
}

VINTED_COLOR_IDS = {
    "Negro": "1",
    "Blanco": "12",
    "Gris": "3",
    "Azul": "9",
    "Rojo": "7",
    "Verde": "10",
    "Amarillo": "8",
    "Naranja": "11",
    "Beige": "4"
}

VINTED_SIZE_IDS = {
    "XS": "206", "S": "207", "M": "208", "L": "209", "XL": "210", "XXL": "211", "3XL": "212",
    "36": "770", "37": "771", "38": "772", "39": "773", "40": "774",
    "41": "775", "42": "776", "43": "777", "44": "778", "45": "779", 
    "46": "780", "47": "781", "48": "782", "49": "783", "50": "784"
}

VINTED_CONDITION_IDS = {
    "Nuevo con etiquetas": "6",
    "Nuevo sin etiquetas": "104",
    "Muy bueno": "2",
    "Bueno": "3",
    "Satisfactorio": "4"
}

BASE_URL = "https://www.vinted.es/catalog"

def build_search_url(config):
    """
    Constructs the Vinted search URL based on SearchConfig object (enhanced).
    """
    query_params = []
    
    # 1. Term / Brand
    # If brand is specified but no term, we search by brand text
    search_text = config.term
    if config.brand_name:
        if search_text:
            search_text += f" {config.brand_name}"
        else:
            search_text = config.brand_name
            
    if search_text:
        query_params.append(f"search_text={search_text.replace(' ', '+')}")
    
    # 2. Price
    if config.min_price is not None: query_params.append(f"price_from={config.min_price}")
    if config.max_price is not None: query_params.append(f"price_to={config.max_price}")
        
    # 3. Size
    if config.sizes:
        for s in config.sizes.split(','):
            if s in VINTED_SIZE_IDS: query_params.append(f"size_ids[]={VINTED_SIZE_IDS[s]}")
            
    # 4. Condition
    if config.condition:
        for c in config.condition.split(','):
            if c in VINTED_CONDITION_IDS: query_params.append(f"status_ids[]={VINTED_CONDITION_IDS[c]}")
    
    # 5. Colors (New)
    if hasattr(config, 'color_ids') and config.color_ids:
        for c_name in config.color_ids.split(','):
            if c_name in VINTED_COLOR_IDS:
                query_params.append(f"color_ids[]={VINTED_COLOR_IDS[c_name]}")

    # 6. Catalogs (New)
    if hasattr(config, 'catalog_ids') and config.catalog_ids:
        for cat_name in config.catalog_ids.split(','):
            if cat_name in VINTED_CATALOG_IDS:
                query_params.append(f"catalog[]={VINTED_CATALOG_IDS[cat_name]}")

    query_params.append("order=newest_first")
    
    url = f"{BASE_URL}?{'&'.join(query_params)}"
    return url

def scrape_vinted(search_config):
    results = []
    term = search_config.term or getattr(search_config, 'brand_name', None) or "Sin término"
    log_to_db(f"Iniciando búsqueda avanzada: {term}")

    search_url = build_search_url(search_config)
    log_to_db(f"URL: {search_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            # Human-like viewport
            viewport={"width": 1366, "height": 768},
            locale="es-ES"
        )
        
        page = context.new_page()
        
        try:
            log_to_db("Navegando a Vinted...", "INFO")
            page.goto(search_url, timeout=60000)
            
            # Anti-bot / Cookie handling
            try:
                page.click('#onetrust-accept-btn-handler', timeout=3000)
            except: pass

            time.sleep(random.uniform(2, 4)) 
            
            # --- PARSING AND PAGINATION ---

            page_idx = 1
            total_items = 0
            
            while True:
                # Breaks if limits reached
                if search_config.max_pages and page_idx > search_config.max_pages:
                    log_to_db(f"Límite de páginas ({search_config.max_pages}) alcanzado.", "INFO")
                    break
                if search_config.max_items and total_items >= search_config.max_items:
                    log_to_db(f"Límite de items ({search_config.max_items}) alcanzado.", "INFO")
                    break

                log_to_db(f"Procesando página {page_idx}...", "INFO")
                
                try:
                    page.wait_for_selector('div[data-testid="grid-item"]', timeout=10000)
                except Exception:
                    log_to_db("No se encontraron más productos o fin de paginación.", "WARNING")
                    break
                
                items = page.query_selector_all('div[data-testid="grid-item"]')
                
                if not items:
                    break
                    
                for item in items:
                    if search_config.max_items and total_items >= search_config.max_items: break
                    
                    try:
                        # --- ROBUST PARSING STRATEGY ---
                        # Title: looking for specific header or class to avoid polluting with price
                        # Usually it is inside a hierarchy. Let's find the product info container.
                        title_cand = ""
                        
                        # Strategy 1: specific data-testid
                        title_el = item.query_selector('[data-testid*="title"]')
                        if title_el:
                            title_cand = title_el.inner_text().strip()
                        else:
                            # Strategy 2: second text block usually (after user info)
                            # This is risky, let's try finding the link which usually contains the title in aria-label or within
                            link_el = item.query_selector('a[data-testid="item-box-overlay"]') or item.query_selector('a')
                            if link_el:
                                title_cand = link_el.get_attribute('title') or ""
                        
                        # Fallback clean-up if title still looks messy
                        # Remove price patterns if accidentally caught
                        if "€" in title_cand:
                            # Heuristic: split by newline and take valid part? 
                            # If parser failed, we might use the image alt text
                            img = item.query_selector('img')
                            if img: title_cand = img.get_attribute('alt') or title_cand

                        url_el = item.query_selector('a')
                        url = url_el.get_attribute('href') if url_el else None
                        
                        # Price
                        price_str = "0.0"
                        # Try finding element with price text content
                        # Vinted prices format: "10,00 €"
                        text_content = item.inner_text()
                        # Regex filter for price might be safer than selecting random divs
                        import re
                        price_match = re.search(r'(\d+[,.]\d{2})\s?€?', text_content)
                        if price_match:
                            price_str = price_match.group(1).replace(',', '.')
                        
                        # Brand (often hidden or in specific footer)
                        brand = item.query_selector('p[data-testid="grid-item-subtitle"]') # Common for brand/size
                        brand_txt = brand.inner_text() if brand else "Desconocida"
                        
                        # Size (sometimes mixed with brand)
                        size_txt = "N/A"
                        # Try to parse from subtitle if format is "Brand / Size"
                        
                        if url:
                            if not url.startswith("http"): url = f"https://www.vinted.es{url}"
                            
                            results.append({
                                'title': title_cand,
                                'price': float(price_str) if price_str else 0.0,
                                'url': url,
                                'image_url': item.query_selector('img').get_attribute('src') if item.query_selector('img') else None,
                                'brand': brand_txt,
                                'size': size_txt
                            })
                            total_items += 1
                            
                    except Exception as e:
                        continue
                
                # Next Page logic
                page_idx += 1
                try:
                    # Generic next button check or URL manipulation
                    # Vinted usually uses URL params, so we can check if "Next" button exists
                    next_btn = page.query_selector('a[data-testid="pagination-next-button"]');
                    if not next_btn or "disabled" in next_btn.get_attribute('class'):
                         break
                    next_btn.click()
                    page.wait_for_timeout(3000) # Wait for load
                    # Alternatively, update URL param logic if loop handles goto
                except:
                    break
            
            browser.close()
            
        except Exception as e:
            log_to_db(f"Error crítico en scraper: {e}", "ERROR")

    log_to_db(f"Búsqueda finalizada. {len(results)} items extraídos.", "INFO")
    return results

def fetch_vinted_brands(keyword=""):
    """
    Scrapes Vinted API/Page to find brands matching a keyword.
    Returns: List of dicts {'id': '123', 'title': 'Nike'}
    """
    brands = []
    log_to_db(f"Buscando marcas: '{keyword}'", "INFO")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use a context to keep cookies if needed, or just new page
        page = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="es-ES"
        )
        
        try:
            # Vinted hidden API for brands usually accessed via:
            # https://www.vinted.es/api/v2/catalog/brands?search_text=nike
            # But access is protected.
            # Alternative: Scrape the HTML search if possible, or use a known public endpoint.
            # Let's try navigating to the catalog page and intercepting the response or using a specific search page.
            
            # METHOD A: Use the autocomplete API endpoint (often easiest if cookies set)
            page.goto("https://www.vinted.es", timeout=30000)
            try: page.click('#onetrust-accept-btn-handler')
            except: pass
            
            # Wait for session
            time.sleep(2)
            
            # Direct API call via page context (to use auth/cookies)
            # URL: /api/v2/catalog/brands?search_text={keyword}
            api_url = f"https://www.vinted.es/api/v2/catalog/brands?search_text={keyword}" if keyword else "https://www.vinted.es/api/v2/catalog/brands"
            
            # JavaScript evaluation to fetch data
            data = page.evaluate(f'''async () => {{
                try {{
                    const response = await fetch("{api_url}");
                    return await response.json();
                }} catch (e) {{
                    return null;
                }}
            }}''')
            
            if data and 'brands' in data:
                for b in data['brands']:
                    brands.append({'id': str(b['id']), 'title': b['title']})
            
            log_to_db(f"Encontradas {len(brands)} marcas.", "INFO")
            
        except Exception as e:
             log_to_db(f"Error buscando marcas: {e}", "ERROR")
             
        browser.close()
        
    return brands

def verify_sold_status(product_url):
    """
    Checks a specific product URL to see if it's sold or deleted.
    Returns: 'sold', 'active', 'deleted'
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        try:
            page.goto(product_url, timeout=30000)
            
            # Check for 'Sold' text (Vinted specific classes or text)
            # Usually strict text search is safest vs Class changes
            content = page.content().lower()
            
            if "vendido" in content or "sold" in content:
                # Need to be careful about false positives in comments/desc.
                # Look for specific sold button or banner
                if page.query_selector('div[data-testid="item-status-banner"]'):
                    return 'sold'
                # Or check the buy button state
                buy_btn = page.query_selector('button[data-testid="item-buy-button"]')
                if not buy_btn:
                    # If no buy button, often sold or reserved
                    return 'sold'
            
            # Check title to ensure page loaded
            if page.title() == "Vinted": # Redirected to home
                return 'deleted'
                
            return 'active'
        except Exception:
            return 'deleted' # Assume deleted if 404/Timeout
        finally:
            browser.close()

if __name__ == "__main__":
    pass# Test function
    class MockConfig:
        term = "nike vintage"
        min_price = 10
        max_price = 50
        sizes = "L,XL"
        
    print(scrape_vinted(MockConfig()))
