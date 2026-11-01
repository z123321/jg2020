# sync_abebooks.py - 修复版：多策略抓取 AbeBooks 价格
import sqlite3
import time
import random
import sys
import re
import json

# 尝试导入 selenium
try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False
    print("警告: 未安装 undetected_chromedriver，将使用 requests 模式")

# 尝试导入 requests + bs4
try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("警告: 未安装 requests/bs4，功能受限")

# ==========================================
# 全局配置
# ==========================================
DB_FILE = "my_books.db"
BATCH_SIZE = 200

driver = None
conn = None

# ==========================================
# 浏览器初始化
# ==========================================
def init_browser():
    global driver
    if not HAS_SELENIUM:
        print("Selenium 不可用，跳过浏览器初始化")
        return
    options = uc.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # 禁用图片加载以加速
    options.add_argument("--blink-settings=imagesEnabled=false")
    
    print("正在启动 Chrome...")
    try:
        driver = uc.Chrome(options=options)
        driver.set_page_load_timeout(30)
        print("Chrome 启动成功！")
    except Exception as e:
        print(f"Chrome 启动失败: {e}，将使用 requests 模式")
        driver = None

# ==========================================
# 数据库初始化
# ==========================================
def init_db():
    global conn
    try:
        conn = sqlite3.connect(DB_FILE)
        print("数据库连接成功")
    except Exception as e:
        print(f"数据库打开失败: {e}")
        sys.exit(1)

# ==========================================
# 策略1: 使用 Selenium 提取（优先）
# ==========================================
def scrape_with_selenium(isbn):
    """使用 Selenium 打开页面，用多种策略提取数据"""
    global driver
    if not driver:
        return None, None, None, None
    
    try:
        url = f"https://www.abebooks.com/servlet/SearchResults?kn={isbn}"
        print(f"  正在打开链接: {url}")
        driver.get(url)
        
        # 检测 Cloudflare
        if "Checking your browser" in driver.title or "access denied" in driver.title:
            print("  触发 Cloudflare 拦截")
            return None, None, None, None
        
        # 等待页面渲染
        time.sleep(5)
        
        # 获取页面文本
        body_text = driver.find_element(By.TAG_NAME, 'body').text
        
        # 策略1a: 从可见文本中提取价格
        price = extract_price_from_text(body_text)
        
        # 策略1b: 从 HTML 源码中提取 JSON-LD
        if not price:
            price, condition, seller, book_url = extract_from_jsonld(driver.page_source, isbn)
            if price:
                return price, condition, seller, book_url
        
        # 策略1c: 从 HTML 源码中用正则提取
        if not price:
            price, condition, seller, book_url = extract_from_html_regex(driver.page_source, isbn)
        
        if price:
            print(f"  匹配成功: 价格={price} | 品相={condition} | 卖家={seller}")
            return price, condition, seller, book_url
        else:
            print(f"  未找到 ISBN: {isbn} 的有效结果")
            return None, None, None, None
            
    except TimeoutException:
        print("  加载超时")
        return None, None, None, None
    except Exception as e:
        print(f"  Selenium 错误: {e}")
        return None, None, None, None

# ==========================================
# 策略2: 使用 requests + BeautifulSoup（备选）
# ==========================================
def scrape_with_requests(isbn):
    """使用 requests 直接请求页面（不启动浏览器）"""
    if not HAS_REQUESTS:
        return None, None, None, None
    
    try:
        url = f"https://www.abebooks.com/servlet/SearchResults?kn={isbn}"
        print(f"  [Requests] 正在请求: {url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        session = requests.Session()
        session.headers.update(headers)
        
        response = session.get(url, timeout=30)
        html = response.text
        
        # 检查是否被拦截
        if 'Checking your browser' in html or 'access denied' in html:
            print("  [Requests] 触发拦截")
            return None, None, None, None
        
        # 解析 HTML
        soup = BeautifulSoup(html, 'html.parser')
        body_text = soup.get_text()
        
        # 提取价格
        price = extract_price_from_text(body_text)
        
        # 提取 JSON-LD
        if not price:
            price, condition, seller, book_url = extract_from_jsonld(html, isbn)
            if price:
                return price, condition, seller, book_url
        
        # 正则提取
        if not price:
            price, condition, seller, book_url = extract_from_html_regex(html, isbn)
        
        if price:
            print(f"  [Requests] 匹配成功: 价格={price}")
            return price, condition, seller, book_url
        else:
            print(f"  [Requests] 未找到有效结果")
            return None, None, None, None
            
    except Exception as e:
        print(f"  [Requests] 错误: {e}")
        return None, None, None, None

# ==========================================
# 价格提取函数
# ==========================================
def extract_price_from_text(text):
    """从页面文本中提取价格"""
    # 匹配常见的价格格式
    # $12.50, USD 12.50, 12.50 USD, £12.50, €12.50
    patterns = [
        r'[\$£€]\s*(\d+\.?\d*)',  # $12.50
        r'(?:USD|EUR|GBP)\s*(\d+\.?\d*)',  # USD 12.50
        r'(\d+\.?\d*)\s*(?:USD|EUR|GBP)',  # 12.50 USD
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                price_val = float(match)
                # 过滤掉不合理的价格（太小的可能是错误匹配）
                if 1.0 <= price_val <= 5000.0:
                    # 检查上下文是否有货币符号
                    idx = text.find(match)
                    context = text[max(0, idx-10):idx+len(match)+10]
                    if '$' in context:
                        return f"${match}"
                    elif 'USD' in context.upper():
                        return f"{match} USD"
                    elif 'EUR' in context.upper():
                        return f"{match} EUR"
                    elif 'GBP' in context.upper():
                        return f"{match} GBP"
                    else:
                        return f"{match}"
            except (ValueError, TypeError):
                continue
    return ""

def extract_from_jsonld(source, isbn):
    """从 JSON-LD 脚本中提取数据"""
    try:
        json_patterns = re.findall(r'<script type="application/ld\+json">(.*?)</script>', source, re.DOTALL)
        
        items = []
        for match in json_patterns:
            try:
                data = json.loads(match)
                if isinstance(data, list):
                    items.extend(data)
                elif isinstance(data, dict):
                    items.append(data)
            except json.JSONDecodeError:
                continue
        
        # 查找书籍相关的条目
        for item in items:
            # 检查是否是 Book 类型
            item_type = item.get('@type', '')
            if 'Book' in str(item_type) or 'Book' in str(item.get('name', '')):
                # 提取价格
                if 'offers' in item:
                    offers = item['offers']
                    if isinstance(offers, dict):
                        price_val = offers.get('price', '')
                        currency = offers.get('priceCurrency', '')
                        if price_val and float(price_val) > 0:
                            condition = item.get('bookEdition', item.get('bookFormat', ''))
                            seller_info = item.get('seller', {})
                            seller = seller_info.get('name', '') if isinstance(seller_info, dict) else str(seller_info)
                            book_url = item.get('url', '')
                            return f"{price_val} {currency}".strip(), str(condition), seller, book_url
                    elif isinstance(offers, list):
                        for offer in offers:
                            if isinstance(offer, dict) and 'price' in offer:
                                price_val = offer['price']
                                currency = offer.get('priceCurrency', '')
                                if price_val and float(price_val) > 0:
                                    condition = item.get('bookEdition', item.get('bookFormat', ''))
                                    seller_info = item.get('seller', {})
                                    seller = seller_info.get('name', '') if isinstance(seller_info, dict) else str(seller_info)
                                    book_url = item.get('url', '')
                                    return f"{price_val} {currency}".strip(), str(condition), seller, book_url
        return "", "", "", ""
    except Exception:
        return "", "", "", ""

def extract_from_html_regex(source, isbn):
    """从 HTML 源码中用正则提取数据"""
    try:
        # 提取价格
        price = ""
        # 匹配 $数字.数字 格式
        price_match = re.search(r'([\$£€])(\d+\.\d{2})', source)
        if price_match:
            currency = price_match.group(1)
            amount = price_match.group(2)
            price = f"{currency}{amount}"
        
        # 提取品相
        condition = ""
        cond_patterns = [
            r'(?:condition|item-condition|item-state)[^>]*>([^<]+)<',
            r'(?:Used|New|Very\s+Good|Good|Acceptable|Like\s+New)',
        ]
        for pat in cond_patterns:
            m = re.search(pat, source, re.IGNORECASE)
            if m:
                condition = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
                break
        
        # 提取链接
        book_url = ""
        link_match = re.search(r'(https://www\.abebooks\.com/servlet/Listing[^"]+)', source)
        if link_match:
            book_url = link_match.group(1)
        
        return price, condition, "", book_url
    except Exception:
        return "", "", "", ""

# ==========================================
# 主抓取函数（多策略）
# ==========================================
def scrape_abebooks(isbn):
    """多策略抓取 AbeBooks 价格信息"""
    # 策略1: Selenium（如果可用）
    if HAS_SELENIUM and driver:
        result = scrape_with_selenium(isbn)
        if result[0]:  # 如果成功提取到价格
            return result
    
    # 策略2: requests + bs4（备选）
    if HAS_REQUESTS:
        result = scrape_with_requests(isbn)
        if result[0]:
            return result
    
    print(f"  所有策略均未成功: {isbn}")
    return None, None, None, None

# ==========================================
# 数据库更新
# ==========================================
def update_book_info(isbn, price, condition, seller, book_url):
    """更新数据库"""
    if not conn:
        return
    cursor = conn.cursor()
    if price is None:
        price = "NO_RESULTS"
    
    cursor.execute("""
        UPDATE books SET 
            abe_price = ?, abe_condition = ?, abe_seller = ?, 
            abe_book_url = ?, abe_updated_at = CURRENT_TIMESTAMP
        WHERE isbn_13 = ?
    """, (price, condition, seller, book_url, isbn))
    conn.commit()

# ==========================================
# 获取待同步批次
# ==========================================
def get_next_batch(limit):
    """获取下一批待同步的 ISBN"""
    if not conn:
        return []
    cursor = conn.cursor()
    cursor.execute("""
        SELECT isbn_13 FROM books 
        WHERE abe_price IS NULL OR abe_price = 'NO_RESULTS' 
        LIMIT ?
    """, (limit,))
    return [row[0] for row in cursor.fetchall()]

# ==========================================
# 入口
# ==========================================
if __name__ == "__main__":
    init_db()
    init_browser()
    
    try:
        while True:
            isbns = get_next_batch(BATCH_SIZE)
            if not isbns:
                print("\n数据库中没有更多待同步的 ISBN 了！")
                break
            
            print(f"\n开始同步本批次的 {len(isbns)} 本书...")
            
            for i, isbn in enumerate(isbns):
                print(f"\n[{i+1}/{len(isbns)}] 处理 ISBN: {isbn}")
                p, c, s, u = scrape_abebooks(isbn)
                update_book_info(isbn, p, c, s, u)
                
                # 随机休息
                if i < len(isbns) - 1:
                    sleep_time = random.uniform(20, 40)
                    print(f"  随机休息 {sleep_time:.1f} 秒...")
                    time.sleep(sleep_time)
    
    except KeyboardInterrupt:
        print("\n脚本已手动停止。")
    except Exception as e:
        print(f"\n运行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        if conn:
            conn.close()
        print("\n程序结束。")
