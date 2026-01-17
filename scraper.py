import logging
import time
import random
from playwright.sync_api import sync_playwright

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONFIGURATION & CONSTANTS ---

# Common Vinted Size IDs (These can change, so keep them accessible)
# Source: Reverse engineering Vinted URLs or mapping from their filter UI
VINTED_SIZE_IDS = {
    "XS": "206",
    "S": "207",
    "M": "208",
    "L": "209",
    "XL": "210",
    "XXL": "211",
    "36": "770",
    "37": "771", 
    "38": "772",
    "39": "773",
    "40": "774",
    "41": "775",
    "42": "776",
    "43": "777",
    "44": "778",
    "45": "779"
}

BASE_URL = "https://www.vinted.es/vetements" # Base URL can vary by region (es, fr, uk, etc.) - Defaulting to ES/Generic

def build_search_url(term, min_price=None, max_price=None, sizes=None):
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
        
    # Size filters (multiple selection allowed)
    if sizes:
        # Expecting sizes to be a list of strings like ["M", "L"] matches keys in VINTED_SIZE_IDS
        for size in sizes:
            if size in VINTED_SIZE_IDS:
                query_params.append(f"size_ids[]={VINTED_SIZE_IDS[size]}")
    
    # Sorting (variable logic, usually by relevance or newest)
    query_params.append("order=newest_first")
    
    url = f"{base}?{'&'.join(query_params)}"
    return url

def scrape_vinted(search_config):
    """
    Scrapes Vinted for a given SearchConfig object.
    Returns a list of dictionaries with product details.
    """
    results = []
    
    term = search_config.term
    min_price = search_config.min_price
    max_price = search_config.max_price
    
    # Parse sizes from comma-separated string
    sizes_list = []
    if search_config.sizes:
        sizes_list = [s.strip() for s in search_config.sizes.split(',')]
        
    search_url = build_search_url(term, min_price, max_price, sizes_list)
    logging.info(f"Scraping URL: {search_url}")

    with sync_playwright() as p:
        # Launch browser with real user agent attributes to avoid bot detection
        browser = p.chromium.launch(headless=True)
        
        # Create a context with specific user agent
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        
        page = context.new_page()
        
        try:
            page.goto(search_url, timeout=60000)
            
            # Specialized Wait logic - Vinted often has cookie banners or anti-bot checks
            # Wait for the grid of items to load
            try:
                # Cookie banner handling (Keep it simple, try to click 'Reject' or 'Accept' if visible)
                # Selectors vary; generic attempt
                button = page.query_selector('button[id*="onetrust-accept-btn-handler"]') 
                if button:
                    button.click()
            except Exception:
                pass # Cookie banner might not appear or selector changed

            time.sleep(3) # Creating a small human-like pause
            
            # Selector for product items (Vinted changes classes often, relying on test-ids or hierarchy is better)
            # Current Vinted structure (approximate) - look for generic item containers
            # A good strategy is looking for anchor tags with product URLs within the feed
            
            # Note: This selector is a best-effort based on typical Vinted DOM. 
            # It might need adjustment if Vinted updates their UI.
            # Usually items are in a grid, inside 'div.feed-grid__item' or similar.
            
            page.wait_for_selector('div[data-testid="grid-item"]', timeout=10000)
            
            items = page.query_selector_all('div[data-testid="grid-item"]')
            
            logging.info(f"Found {len(items)} items on the page.")
            
            for item in items[:20]: # Limit to first 20 for 'latest' items per search to save resource
                try:
                    product_data = {}
                    
                    # Link & Title
                    link_elem = item.query_selector('a')
                    if not link_elem:
                        continue
                        
                    url_suffix = link_elem.get_attribute('href')
                    product_data['url'] = url_suffix # Vinted search often gives absolute or relative URLs
                    if not product_data['url'].startswith('http'):
                        product_data['url'] = f"https{product_data['url']}" if product_data['url'].startswith('://') else f"https://www.vinted.es{product_data['url']}"
                        
                    product_data['title'] = link_elem.get_attribute('title') or "No Title"
                    
                    # Price (Usually in a specific div or span text)
                    # Helper to find price text
                    price_elem = item.query_selector('p[class*="web_ui__Text__title"]')
                    # Fallback text search if class is dynamic
                    if not price_elem:
                        # Try to find text with currency symbol
                        pass 
                        
                    if price_elem:
                        raw_price = price_elem.inner_text()
                        # Clean price string "20,00 €" -> 20.00
                        clean_price = raw_price.replace('€', '').replace(' ', '').replace(',', '.')
                        try:
                            product_data['price'] = float(clean_price)
                        except:
                            product_data['price'] = 0.0
                    else:
                        product_data['price'] = 0.0

                    # Brand and Size are often in subtitle texts
                    # This implies parsing the item's details more deeply or grabbing the subtitles
                    # For listing page, Vinted usually shows Brand then Size in smaller text
                    meta_texts = item.query_selector_all('p[class*="web_ui__Text__muted"]')
                    if len(meta_texts) >= 2:
                        product_data['size'] = meta_texts[0].inner_text()
                        product_data['brand'] = meta_texts[1].inner_text()
                    elif len(meta_texts) == 1:
                        product_data['brand'] = meta_texts[0].inner_text()
                        product_data['size'] = "N/A"
                    else:
                        product_data['brand'] = "Unknown"
                        product_data['size'] = "N/A"

                    # Image
                    img_elem = item.query_selector('img')
                    if img_elem:
                         product_data['image_url'] = img_elem.get_attribute('src')

                    results.append(product_data)
                    
                except Exception as e:
                    logging.warning(f"Error parsing item: {e}")
                    continue
                    
        except Exception as e:
            logging.error(f"Error scraping {term}: {e}")
            
        browser.close()
        
    return results

if __name__ == "__main__":
    # Test function
    class MockConfig:
        term = "nike vintage"
        min_price = 10
        max_price = 50
        sizes = "L,XL"
        
    print(scrape_vinted(MockConfig()))
