import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import json
import time
import random
import sqlite3
import os

# ===== 配置区 =====
PROXY = "http://127.0.0.1:7890"
SEARCH_KEYWORD = "To Kill a Mockingbird"
MAX_PAGES = 3
MAX_RETRIES = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# ===== 数据库 =====
DB_NAME = "abebooks_prices.db"
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS abebooks_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT, title TEXT, author TEXT, price_text TEXT,
    currency TEXT, condition TEXT, seller TEXT, book_url TEXT,
    fetched_at TEXT, isbn TEXT, format TEXT, publisher TEXT,
    pub_date TEXT, seller_url TEXT, image_url TEXT,
    price_value REAL,
    UNIQUE(isbn, price_text, keyword),
    UNIQUE(title, price_text, keyword)
)
""")
# 兼容旧表：如果缺少 price_value 列则自动添加
try:
    c.execute("SELECT price_value FROM abebooks_prices LIMIT 1")
except sqlite3.OperationalError:
    c.execute("ALTER TABLE abebooks_prices ADD COLUMN price_value REAL")
    conn.commit()
conn.commit()

def insert_data(row):
    try:
        keys = [
            "keyword", "title", "author", "price_text", "currency",
            "condition", "seller", "book_url", "fetched_at",
            "isbn", "format", "publisher", "pub_date", "seller_url", "image_url"
        ]
        vals = tuple(str(row.get(k, "")) for k in keys)
        c.execute(
            f"INSERT OR IGNORE INTO abebooks_prices ({','.join(keys)}) VALUES ({','.join(['?']*len(keys))})",
            vals
        )
        conn.commit()
        return c.rowcount > 0
    except sqlite3.IntegrityError:
        return False

def _simplify(val):
    if not isinstance(val, str): return val
    return val.rsplit("/", 1)[-1]

def _safe_str(val):
    if isinstance(val, (dict, list)): return str(val)
    return "" if val is None else str(val)

def parse_jsonld_items(items):
    results = []
    for item in items:
        try:
            title = item.get("name", "")
            authors = item.get("author", [])
            author = ", ".join(a.get("name","") for a in authors if isinstance(a, dict) and a.get("name")) if isinstance(authors, list) else (authors.get("name","") if isinstance(authors, dict) else "")
            offers = item.get("offers", {})
            price_text, currency = "", ""
            if isinstance(offers, dict):
                price_text, currency = str(offers.get("price","")), offers.get("priceCurrency","")
            elif isinstance(offers, list) and offers:
                price_text, currency = str(offers[0].get("price","")), offers[0].get("priceCurrency","")
            book_url = item.get("url", "")
            img = item.get("image", "")
            image_url = img[0] if isinstance(img, list) and img else (img if isinstance(img, str) else "")
            offered_by = item.get("offeredBy", {})
            seller_name = offered_by.get("name","") if isinstance(offered_by, dict) else ""
            seller_url = offered_by.get("url","") if isinstance(offered_by, dict) else ""
            publisher_raw = item.get("publisher", "")
            if isinstance(publisher_raw, dict): publisher_raw = publisher_raw.get("name","")
            elif isinstance(publisher_raw, list):
                publisher_raw = publisher_raw[0] if publisher_raw else ""
                if isinstance(publisher_raw, dict): publisher_raw = publisher_raw.get("name","")
            pub_date_raw = item.get("datePublished", "")
            if isinstance(pub_date_raw, dict): pub_date_raw = str(pub_date_raw)
            results.append({
                "title": title, "author": author, "price_text": str(price_text),
                "currency": currency, "condition": _simplify(item.get("itemCondition","")),
                "seller": seller_name, "book_url": book_url,
                "isbn": _safe_str(item.get("isbn","")), "format": _simplify(item.get("bookFormat","")),
                "publisher": _safe_str(publisher_raw), "pub_date": _safe_str(pub_date_raw),
                "seller_url": seller_url, "image_url": image_url,
            })
        except Exception as e:
            print(f"    解析单条出错: {e}")
    return results

# ===== 浏览器 =====
ua = random.choice(USER_AGENTS)
width, height = random.randint(1024,1920), random.randint(768,1080)
print(f"[启动] UA={ua[:60]}... 窗口={width}x{height}")
options = uc.ChromeOptions()
options.add_argument(f"--proxy-server={PROXY}")
options.add_argument("--no-sandbox"); options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu"); options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument(f"--window-size={width},{height}"); options.add_argument(f"--user-agent={ua}")
print("正在启动 Chrome...")
browser = uc.Chrome(options=options)
wait = WebDriverWait(browser, 20)
print("启动成功！")

def human_delay(lo=3, hi=7):
    time.sleep(random.uniform(lo, hi))

def scroll_page():
    for _ in range(random.randint(3,6)):
        browser.execute_script(f"window.scrollBy(0, {random.randint(200,600)});")
        time.sleep(random.uniform(0.3,0.8))

def scrape_abebooks(keyword, max_pages=3):
    print(f"\n{'='*50}\n开始抓取 AbeBooks 关键词：{keyword}\n{'='*50}\n")
    base_url = f"https://www.abebooks.com/servlet/SearchResults?kn={keyword.replace(' ','+')}"
    page = 1
    while page <= max_pages:
        url = f"{base_url}&pn={page}"
        attempt, success = 0, False
        while attempt < MAX_RETRIES and not success:
            if attempt > 0:
                backoff = random.uniform(5,10) * attempt
                print(f"  第{attempt}次重试，等待 {backoff:.1f}s...")
                time.sleep(backoff)
            try:
                print(f"正在打开第 {page}/{max_pages} 页 (尝试 {attempt+1}/{MAX_RETRIES})...")
                print(f"URL: {url}")
                browser.get(url)
                human_delay(4, 8); scroll_page(); human_delay(1, 3)
                with open("abebooks_debug.html","w",encoding="utf-8") as f: f.write(browser.page_source)
                page_title = browser.title
                print(f"  页面标题: {page_title}")
                if "robot" in page_title.lower() or "access denied" in page_title.lower() or len(browser.page_source)<2000:
                    print("  ⚠ 可能触发反爬，重试..."); attempt += 1; continue
                soup = BeautifulSoup(browser.page_source, "html.parser")
                jsonld_script = soup.find("script", id="item-list", type="application/ld+json")
                print(f"  包含JSON-LD: {jsonld_script is not None}")
                rows = []
                if jsonld_script:
                    print("  [策略1] JSON-LD 提取...")
                    try:
                        data = json.loads(jsonld_script.string)
                        items = []
                        if isinstance(data, dict):
                            if "itemListElement" in data:
                                for elem in data["itemListElement"]:
                                    if isinstance(elem, dict) and "item" in elem:
                                        book = elem["item"]
                                        if isinstance(book, dict) and book.get("@type") == "Book": items.append(book)
                            elif "@graph" in data:
                                items = [i for i in data["@graph"] if isinstance(i, dict) and i.get("@type") == "Book"]
                            elif data.get("@type") == "Book": items = [data]
                        elif isinstance(data, list):
                            items = [i for i in data if isinstance(i, dict) and i.get("@type") == "Book"]
                        print(f"  JSON-LD 找到 {len(items)} 条结果")
                        rows = parse_jsonld_items(items)
                    except Exception as e:
                        print(f"  JSON-LD 解析失败: {e}")
                if not rows:
                    print("  [策略2] HTML 选择器回退...")
                    items = browser.find_elements(By.CSS_SELECTOR, "[data-cy='result-item'], .result-item, .sr-item, .result-row")
                    if not items:
                        print("  未找到商品卡片，请检查 abebooks_debug.html"); attempt += 1; continue
                    print(f"  找到 {len(items)} 个结果块")
                    for item in items:
                        try:
                            title_el = item.find_element(By.CSS_SELECTOR, "a[data-cy='result-title'], .title a, .result-title a, h3 a")
                            price_el = item.find_elements(By.CSS_SELECTOR, ".price, .result-price, [data-cy='result-price']")
                            price_text = price_el[0].text.strip() if price_el else ""
                            currency = "GBP" if "£" in price_text else ("EUR" if "€" in price_text or "EUR" in price_text else ("USD" if "$" in price_text or "USD" in price_text else ""))
                            rows.append({
                                "title": title_el.text.strip(), "author": "", "price_text": price_text,
                                "currency": currency, "condition": "", "seller": "",
                                "book_url": title_el.get_attribute("href") or "",
                                "isbn":"","format":"","publisher":"","pub_date":"","seller_url":"","image_url":"",
                            })
                        except Exception: continue
                if not rows:
                    print("所有策略均未提取到数据，重试..."); attempt += 1; continue
                success = True
                print(f"本页匹配到 {len(rows)} 条数据，开始解析...\n")
                inserted = 0
                for row in rows:
                    row["keyword"] = keyword; row["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if insert_data(row):
                        print(f"  [入库] {row['title']} | {row['price_text']} {row['currency']}"); inserted += 1
                    else:
                        print(f"  [跳过] {row['title']}")
                print(f"  本页新增 {inserted} 条\n")
            except (TimeoutException, WebDriverException) as e:
                print(f"  浏览器异常: {e}"); attempt += 1
            except Exception as e:
                import traceback; traceback.print_exc(); attempt += 1
        if not success:
            print(f"\n⚠ 第{page}页多次重试失败，停止抓取。"); break
        if page >= max_pages:
            print("已达到最大页数，抓取结束。"); break
        try:
            next_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a[rel='next'], .next-page a, .pagination-next a, #nextPage")))
            browser.execute_script("arguments[0].scrollIntoView();", next_btn)
            human_delay(1, 3); next_btn.click(); human_delay(4, 8); page += 1
        except TimeoutException:
            print("  未找到下一页按钮，尝试URL翻页...")
            if page < max_pages: page += 1
            else: break
    c.execute("SELECT COUNT(*) FROM abebooks_prices")
    total = c.fetchone()[0]
    print(f"\n{'='*50}\n抓取完成！共插入 {total} 条数据。\n{'='*50}")

import undetected_chromedriver as uc
uc.Chrome.__del__ = lambda self: None
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import json
import time
import random
import sqlite3
import re
import os

# ===== 配置区 =====
PROXY = "http://127.0.0.1:7890"
SEARCH_KEYWORD = "To Kill a Mockingbird"
MAX_PAGES = 3
MAX_RETRIES = 3

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# ===== 数据库 =====
DB_NAME = "abebooks_prices.db"
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS abebooks_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT, title TEXT, author TEXT, price_text TEXT,
    currency TEXT, condition TEXT, seller TEXT, book_url TEXT,
    fetched_at TEXT, isbn TEXT, format TEXT, publisher TEXT,
    pub_date TEXT, seller_url TEXT, image_url TEXT,
    price_value REAL,
    UNIQUE(isbn, price_text, keyword),
    UNIQUE(title, price_text, keyword)
)
""")
conn.commit()

# ===== 标题过滤 =====
IRRELEVANT_TITLES = {
    "study guide", "instructional guide", "novel study", "lit guide",
    "l-i-t guide", "glossary", "annotations", "textbook",
    "screenplay", "screenplays", "the story of", "biography",
    "companion", "critical companion", "reader's companion",
}

def is_relevant_book(title, keyword):
    """判断这本书是否真的是在搜的目标书，而非蹭关键词的周边产品"""
    if not title:
        return False
    t = title.lower()
    k = keyword.lower()

    # 标题里完全不含关键词，直接过滤
    if k not in t:
        return False

    # 命中干扰词列表，过滤
    for word in IRRELEVANT_TITLES:
        if word in t:
            return False

    return True

# ===== 入库 =====
def insert_data(row):
    try:
        # 解析价格数字，方便后续比价
        price_val = None
        if row.get("price_text"):
            m = re.search(r'([\d,]+\.?\d*)', row["price_text"])
            if m:
                price_val = float(m.group(1).replace(",", ""))

        c.execute("""
            INSERT OR IGNORE INTO abebooks_prices
            (keyword, title, author, price_text, currency, condition,
             seller, book_url, fetched_at, isbn, format, publisher,
             pub_date, seller_url, image_url, price_value)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row.get("keyword",""), row.get("title",""), row.get("author",""),
            row.get("price_text",""), row.get("currency",""), row.get("condition",""),
            row.get("seller",""), row.get("book_url",""), row.get("fetched_at",""),
            row.get("isbn",""), row.get("format",""), row.get("publisher",""),
            row.get("pub_date",""), row.get("seller_url",""), row.get("image_url",""),
            price_val,
        ))
        conn.commit()
        return c.rowcount > 0
    except sqlite3.IntegrityError:
        return False

def _simplify(val):
    if not isinstance(val, str): return val
    return val.rsplit("/", 1)[-1]

def _safe_str(val):
    if isinstance(val, (dict, list)): return str(val)
    return "" if val is None else str(val)

def parse_jsonld_items(items):
    results = []
    for item in items:
        try:
            title = item.get("name", "")
            authors = item.get("author", [])
            author = ", ".join(a.get("name","") for a in authors if isinstance(a, dict) and a.get("name")) if isinstance(authors, list) else (authors.get("name","") if isinstance(authors, dict) else "")
            offers = item.get("offers", {})
            price_text, currency = "", ""
            if isinstance(offers, dict):
                price_text, currency = str(offers.get("price","")), offers.get("priceCurrency","")
            elif isinstance(offers, list) and offers:
                price_text, currency = str(offers[0].get("price","")), offers[0].get("priceCurrency","")
            book_url = item.get("url", "")
            img = item.get("image", "")
            image_url = img[0] if isinstance(img, list) and img else (img if isinstance(img, str) else "")
            offered_by = item.get("offeredBy", {})
            seller_name = offered_by.get("name","") if isinstance(offered_by, dict) else ""
            seller_url = offered_by.get("url","") if isinstance(offered_by, dict) else ""
            publisher_raw = item.get("publisher", "")
            if isinstance(publisher_raw, dict): publisher_raw = publisher_raw.get("name","")
            elif isinstance(publisher_raw, list):
                publisher_raw = publisher_raw[0] if publisher_raw else ""
                if isinstance(publisher_raw, dict): publisher_raw = publisher_raw.get("name","")
            pub_date_raw = item.get("datePublished", "")
            if isinstance(pub_date_raw, dict): pub_date_raw = str(pub_date_raw)
            results.append({
                "title": title, "author": author, "price_text": str(price_text),
                "currency": currency, "condition": _simplify(item.get("itemCondition","")),
                "seller": seller_name, "book_url": book_url,
                "isbn": _safe_str(item.get("isbn","")), "format": _simplify(item.get("bookFormat","")),
                "publisher": _safe_str(publisher_raw), "pub_date": _safe_str(pub_date_raw),
                "seller_url": seller_url, "image_url": image_url,
            })
        except Exception as e:
            print(f"    解析单条出错: {e}")
    return results

# ===== 浏览器 =====
ua = random.choice(USER_AGENTS)
width, height = random.randint(1024,1920), random.randint(768,1080)
print(f"[启动] UA={ua[:60]}... 窗口={width}x{height}")
options = uc.ChromeOptions()
options.add_argument(f"--proxy-server={PROXY}")
options.add_argument("--no-sandbox"); options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu"); options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument(f"--window-size={width},{height}"); options.add_argument(f"--user-agent={ua}")
print("正在启动 Chrome...")
browser = uc.Chrome(options=options)
wait = WebDriverWait(browser, 20)
print("启动成功！")

def human_delay(lo=3, hi=7):
    time.sleep(random.uniform(lo, hi))

def scroll_page():
    for _ in range(random.randint(3,6)):
        browser.execute_script(f"window.scrollBy(0, {random.randint(200,600)});")
        time.sleep(random.uniform(0.3,0.8))

def scrape_abebooks(keyword, max_pages=3):
    print(f"\n{'='*50}\n开始抓取 AbeBooks 关键词：{keyword}\n{'='*50}\n")
    base_url = f"https://www.abebooks.com/servlet/SearchResults?kn={keyword.replace(' ','+')}"
    page = 1
    while page <= max_pages:
        url = f"{base_url}&pn={page}"
        attempt, success = 0, False
        seen_this_page = set()  # 本页内存去重
        while attempt < MAX_RETRIES and not success:
            if attempt > 0:
                backoff = random.uniform(5,10) * attempt
                print(f"  第{attempt}次重试，等待 {backoff:.1f}s...")
                time.sleep(backoff)
            try:
                print(f"正在打开第 {page}/{max_pages} 页 (尝试 {attempt+1}/{MAX_RETRIES})...")
                print(f"URL: {url}")
                browser.get(url)
                human_delay(4, 8); scroll_page(); human_delay(1, 3)
                with open("abebooks_debug.html","w",encoding="utf-8") as f: f.write(browser.page_source)
                page_title = browser.title
                print(f"  页面标题: {page_title}")
                if "robot" in page_title.lower() or "access denied" in page_title.lower() or len(browser.page_source)<2000:
                    print("  ⚠ 可能触发反爬，重试..."); attempt += 1; continue
                soup = BeautifulSoup(browser.page_source, "html.parser")
                jsonld_script = soup.find("script", id="item-list", type="application/ld+json")
                print(f"  包含JSON-LD: {jsonld_script is not None}")
                rows = []
                if jsonld_script:
                    print("  [策略1] JSON-LD 提取...")
                    try:
                        data = json.loads(jsonld_script.string)
                        items = []
                        if isinstance(data, dict):
                            if "itemListElement" in data:
                                for elem in data["itemListElement"]:
                                    if isinstance(elem, dict) and "item" in elem:
                                        book = elem["item"]
                                        if isinstance(book, dict) and book.get("@type") == "Book": items.append(book)
                            elif "@graph" in data:
                                items = [i for i in data["@graph"] if isinstance(i, dict) and i.get("@type") == "Book"]
                            elif data.get("@type") == "Book": items = [data]
                        elif isinstance(data, list):
                            items = [i for i in data if isinstance(i, dict) and i.get("@type") == "Book"]
                        print(f"  JSON-LD 找到 {len(items)} 条结果")
                        rows = parse_jsonld_items(items)
                    except Exception as e:
                        print(f"  JSON-LD 解析失败: {e}")
                if not rows:
                    print("  [策略2] HTML 选择器回退...")
                    items = browser.find_elements(By.CSS_SELECTOR, "[data-cy='result-item'], .result-item, .sr-item, .result-row")
                    if not items:
                        print("  未找到商品卡片，请检查 abebooks_debug.html"); attempt += 1; continue
                    print(f"  找到 {len(items)} 个结果块")
                    for item in items:
                        try:
                            title_el = item.find_element(By.CSS_SELECTOR, "a[data-cy='result-title'], .title a, .result-title a, h3 a")
                            price_el = item.find_elements(By.CSS_SELECTOR, ".price, .result-price, [data-cy='result-price']")
                            price_text = price_el[0].text.strip() if price_el else ""
                            currency = "GBP" if "£" in price_text else ("EUR" if "€" in price_text or "EUR" in price_text else ("USD" if "$" in price_text or "USD" in price_text else ""))
                            rows.append({
                                "title": title_el.text.strip(), "author": "", "price_text": price_text,
                                "currency": currency, "condition": "", "seller": "",
                                "book_url": title_el.get_attribute("href") or "",
                                "isbn":"","format":"","publisher":"","pub_date":"","seller_url":"","image_url":"",
                            })
                        except Exception: continue
                if not rows:
                    print("所有策略均未提取到数据，重试..."); attempt += 1; continue
                success = True
                print(f"本页匹配到 {len(rows)} 条数据，开始解析...\n")
                inserted = 0
                skipped_filter = 0
                skipped_dup = 0
                for row in rows:
                    row["keyword"] = keyword; row["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

                    # 标题过滤
                    if not is_relevant_book(row.get("title",""), keyword):
                        skipped_filter += 1
                        print(f"  [过滤] {row['title']}")
                        continue

                    # 内存去重（同页同标题同价格只入一次）
                    dedup_key = (keyword, row.get("title",""), row.get("price_text",""))
                    if dedup_key in seen_this_page:
                        skipped_dup += 1
                        continue
                    seen_this_page.add(dedup_key)

                    if insert_data(row):
                        print(f"  [入库] {row['title']} | {row['price_text']} {row['currency']}"); inserted += 1
                    else:
                        skipped_dup += 1
                        print(f"  [跳过] {row['title']}")
                print(f"  本页新增 {inserted} 条 | 过滤 {skipped_filter} 条 | 去重 {skipped_dup} 条\n")
            except (TimeoutException, WebDriverException) as e:
                print(f"  浏览器异常: {e}"); attempt += 1
            except Exception as e:
                import traceback; traceback.print_exc(); attempt += 1
        if not success:
            print(f"\n⚠ 第{page}页多次重试失败，停止抓取。"); break
        if page >= max_pages:
            print("已达到最大页数，抓取结束。"); break
        try:
            next_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a[rel='next'], .next-page a, .pagination-next a, #nextPage")))
            browser.execute_script("arguments[0].scrollIntoView();", next_btn)
            human_delay(1, 3); next_btn.click(); human_delay(4, 8); page += 1
        except TimeoutException:
            print("  未找到下一页按钮，尝试URL翻页...")
            if page < max_pages: page += 1
            else: break
    c.execute("SELECT COUNT(*) FROM abebooks_prices")
    total = c.fetchone()[0]
    print(f"\n{'='*50}\n抓取完成！共 {total} 条数据。\n{'='*50}")

import gc

try:
    scrape_abebooks(SEARCH_KEYWORD, MAX_PAGES)
except KeyboardInterrupt:
    print("\n已中断。")
finally:
    try:
        browser.quit()
    except Exception:
        pass
    browser = None
    gc.collect()
    conn.close()
    print("已关闭浏览器和数据库连接。")