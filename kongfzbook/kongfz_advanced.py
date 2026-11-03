import time
import random
import sqlite3
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from lxml import etree

# 1. 数据库初始化
DB_NAME = 'prices.db'
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

c.execute('''
    CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT NOT NULL,
        title TEXT,
        author TEXT,
        price REAL,
        condition TEXT,
        seller TEXT,
        shop_url TEXT,
        fetched_at TEXT
    )
''')
try:
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_unique ON prices (keyword, title, price)')
    conn.commit()
except sqlite3.IntegrityError:
    pass 

def insert_data(keyword, title, author, price, condition, seller, shop_url, fetched_at):
    try:
        c.execute("INSERT INTO prices (keyword, title, author, price, condition, seller, shop_url, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (keyword, title, author, price, condition, seller, shop_url, fetched_at))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

# 2. 初始化浏览器（先弹出窗口让你看到它在正常翻书）
from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from webdriver_manager.microsoft import EdgeChromiumDriverManager
import time

# Edge 浏览器专属配置
edge_options = EdgeOptions()
edge_options.add_argument('--no-sandbox')
edge_options.add_argument('--disable-dev-shm-usage')
edge_options.add_argument('--disable-gpu')
edge_options.add_argument('--disable-blink-features=AutomationControlled')
# 保持有头模式方便调试，如果跑通了后续可以加上 edge_options.add_argument('--headless')

# 自动匹配并启动 Edge 浏览器
browser = webdriver.Edge(
    service=EdgeService(EdgeChromiumDriverManager().install()),
    options=edge_options
)

wait = WebDriverWait(browser, 10)
def scrape_keyword(keyword, max_pages=1):
    print(f"\n{'='*40}")
    print(f"🚀 正在深挖关键词：[{keyword}]")
    print(f"{'='*40}")
    
    # ✅ 修正了 URL 拼接方式
    base_url = f"https://search.kongfz.com/product_result/?key={keyword}"
    current_page = 1
    
    while current_page <= max_pages:
        try:
            print(f"🔍 正在抓取第 {current_page}/{max_pages} 页...")
            url = f"{base_url}&page={current_page}"
            browser.get(url) # ✅ 直接使用拼接好的完整 URL
            
            time.sleep(5) 
            
            # 保存调试网页
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(browser.page_source)
            print(f"⚡ 已保存当前页面为 debug_page.html")

            # 打印网页里的价格数量，用来测试是否拿到了数据
            price_count = browser.page_source.count('<span class="price">')
            print(f"👀 当前页面包含 <span class=\"price\"> 的数量: {price_count}")

            html = etree.HTML(browser.page_source)
            
            # 尝试匹配商品列表
            items = html.xpath('//div[contains(@class,"search-item")] | //li[contains(@class,"goods-item")] | //div[@class="item-list"]/div')
            
            if not items:
                print(f"❌ 未匹配到商品列表！请打开 debug_page.html 查看。")
                break

            print(f"📌 成功匹配到 {len(items)} 条数据，开始提取...")
            for item in items:
                try:
                    title_tag = item.xpath('.//a[@class="bookname"] | .//div[@class="title"]/a | .//span[@class="title"]')
                    title = title_tag[0].text_content().strip() if title_tag else None
                    
                    if title and "登录" not in title and "验证码" not in title:
                        href = title_tag[0].get('href', '') if title_tag else ''
                        author_tag = item.xpath('.//div[contains(@class,"author")]/text()')
                        author = author_tag[0].strip() if author_tag else ''
                        price_tag = item.xpath('.//span[@class="price"]/text()')
                        price_str = price_tag[0].strip().replace('¥', '').replace('元', '').replace(' ', '') if price_tag else '0'
                        price = float(price_str) if price_str else 0.0
                        
                        # 写入数据库
                        fetched_at = time.strftime("%Y-%m-%d %H:%M:%S")
                        if insert_data(keyword, title, author, price, '', '', href, fetched_at):
                            print(f"   [入库成功] {title} | ¥{price}")
                        else:
                            print(f"   [已存在] {title}")
                except Exception as e:
                    continue

            # 翻页逻辑
            next_btn = browser.find_elements(By.LINK_TEXT, "下一页")
            if next_btn and current_page < max_pages:
                browser.execute_script("arguments[0].click();", next_btn[0])
                time.sleep(random.uniform(3, 6))
                current_page += 1
            else:
                print(f"✅ 关键词 [{keyword}] 已无更多页面。")
                break
                
        except Exception as e:
            print(f"❌ 发生错误: {e}")
            break