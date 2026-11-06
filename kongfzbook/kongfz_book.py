import time
import sqlite3
import re
from urllib.parse import quote
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def init_db():
    conn = sqlite3.connect('prices.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            title TEXT,
            author TEXT,
            price REAL,
            condition TEXT,
            seller TEXT,
            shop_url TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn


def parse_zl_field(card, name):
    """从 zl-info-item 里按字段名取作者/出版社等"""
    for it in card.select('.zl-info-item'):
        n = it.select_one('.zl-info-name')
        v = it.select_one('.zl-info-value')
        if n and name in n.get_text():
            return v.get_text(strip=True) if v else '未知'
    return '未知'


def parse_price(card):
    """价格 = price-int + . + price-float"""
    pi = card.select_one('.price-int')
    if not pi:
        return None
    pf = card.select_one('.price-float')
    txt = pi.get_text(strip=True) + ('.' + pf.get_text(strip=True) if pf else '')
    try:
        return float(txt)
    except ValueError:
        return None


def parse_results(html):
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    for card in soup.select('.product-item-wrap'):
        a = card.select_one('.item-name a')
        title = a.get_text(strip=True) if a else '未知'
        shop_url = a.get('href', '') if a else ''
        price = parse_price(card)
        if price is None:
            continue
        shop = card.select_one('.shop-name')
        cond = card.select_one('.quality-info')
        results.append({
            'keyword': '',
            'title': title,
            'author': parse_zl_field(card, '作者'),
            'price': price,
            'condition': cond.get_text(strip=True) if cond else '未知',
            'seller': shop.get_text(strip=True) if shop else '未知',
            'shop_url': shop_url,
        })
    return results


def scrape_kongfz(keyword, delay=5):
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/131.0.0.0 Safari/537.36'
    )

    driver = webdriver.Edge(options=options)
    url = f'https://search.kongfz.com/product_result/?key={quote(keyword)}'
    print(f'正在搜索: {keyword}')

    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.product-item-wrap'))
        )
        time.sleep(2)

        html = driver.page_source
        results = parse_results(html)
        print(f'  抓到 {len(results)} 条')
        time.sleep(delay)
        return results

    except Exception as e:
        print(f'抓取失败: {e}')
        return []
    finally:
        driver.quit()


def save_to_db(conn, records):
    c = conn.cursor()
    for r in records:
        c.execute('''
INSERT OR IGNORE INTO prices (keyword, title, author, price, condition, seller, shop_url)
VALUES (?, ?, ?, ?, ?, ?, ?)
''', (r['keyword'], r['title'], r['author'], r['price'],
      r['condition'], r['seller'], r['shop_url']))

    conn.commit()
    print(f'已保存 {len(records)} 条记录')


def main():
    conn = init_db()
    keywords = []
    try:
        with open("keywords.txt", "r", encoding="utf-8") as f:
            keywords = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("keywords.txt 不存在，使用默认关键词列表")
        keywords = ['', '红楼 梦','活着','西游记','三国演义']
    print(f"本次待爬关键词：{keywords}")
    
    all_records = []
    for kw in keywords:
        records = scrape_kongfz(kw, delay=5)
        for r in records:
            r['keyword'] = kw        # 补上关键词
        all_records.extend(records)
        for r in records:
            print(f"  ￥{r['price']} | {r['condition']} | {r['seller']} | {r['title']}")
        if not records:
            print('  未找到结果')

    if all_records:
        save_to_db(conn, all_records)

    conn.close()
    print('完成！')


if __name__ == '__main__':
    main()