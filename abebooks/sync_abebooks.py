import requests
import sqlite3
import time
import re
import json

# ============ 配置 ============
DB_PATH = 'books.db'  # 请确认你的数据库文件名
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'https://www.abebooks.com/',
}
REQUEST_DELAY = 3  # 请求间隔秒数

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def extract_isbn_from_url(image_url):
    """从图片URL中提取ISBN"""
    if not image_url:
        return None
    m = re.search(r'/isbn/(\d{10,13})-', image_url)
    return m.group(1) if m else None

def get_isbn_from_listing(listing_id):
    """通过listingId获取详情页ISBN（备用方案）"""
    url = f"https://www.abebooks.com/servlet/BookDetailsPL?bi={listing_id}"
    try:
        resp = SESSION.get(url, timeout=15)
        # 尝试多种正则匹配ISBN
        m = re.search(r'"isbn["\s:]+(\d{10,13})', resp.text)
        if m: return m.group(1)
        m = re.search(r'<meta[^>]*property="books:isbn"[^>]*content="(\d{10,13})"', resp.text)
        if m: return m.group(1)
    except:
        pass
    return None

def search_abebooks(title):
    """搜索AbeBooks"""
    url = "https://www.abebooks.com/servlet/HighlightInventory"
    params = {
        'rollup': 'on', 'dym': 'on', 'ds': 30, 'sortby': 20, 'kn': title
    }
    try:
        resp = SESSION.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  请求出错: {e}")
        return []

    # 合并不同排序的结果
    items = []
    seen = set()
    for key in data.get('highlightedItemsMap', {}):
        for item in data['highlightedItemsMap'][key]:
            lid = item['listingId']
            if lid not in seen:
                seen.add(lid)
                items.append(item)
    return items

def normalize_isbn(isbn):
    """标准化ISBN：去掉横杠和空格"""
    if not isbn: return None
    return re.sub(r'[\s-]', '', str(isbn))

def extract_edition(title_str):
    """从标题中提取版本，如 '2nd Edition' -> '2nd'"""
    if not title_str: return "unknown"
    m = re.search(r'(\d+(?:st|nd|rd|th))\s*edition', title_str, re.IGNORECASE)
    return m.group(1).lower() if m else "unknown"

def update_prices():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 请根据你的实际表结构修改这里
    cur.execute("SELECT isbn, title FROM books WHERE abe_prices_json IS NULL LIMIT 10")
    rows = cur.fetchall()
    
    if not rows:
        print("没有需要更新的书籍。")
        conn.close()
        return

    print(f"开始处理 {len(rows)} 本书籍...\n")

    for isbn_raw, title in rows:
        target_isbn = normalize_isbn(isbn_raw)
        print(f"正在搜索: {title} (ISBN: {target_isbn})")

        items = search_abebooks(title)
        price_map = {}

        for item in items:
            # 优先从图片URL获取ISBN
            item_isbn = extract_isbn_from_url(item.get('imageUrl', ''))
            
            # 如果图片里没有，再请求详情页
            if not item_isbn:
                item_isbn = get_isbn_from_listing(item['listingId'])
                time.sleep(1) # 请求详情页时额外停顿一下

            if item_isbn and normalize_isbn(item_isbn) == target_isbn:
                edition = extract_edition(item.get('title', ''))
                total_price = item['priceValue'] + item.get('shippingValue', 0)
                
                # 只保留每个版本的最低价
                if edition not in price_map or total_price < price_map[edition]:
                    price_map[edition] = round(total_price, 2)

        if price_map:
            prices_json = json.dumps(price_map, ensure_ascii=False)
            print(f"  ✅ 更新成功: {prices_json}")
            cur.execute("UPDATE books SET abe_prices_json=? WHERE isbn=?", (prices_json, isbn_raw))
            conn.commit()
        else:
            print(f"  ❌ 未找到匹配项")

        time.sleep(REQUEST_DELAY)

    conn.close()
    print("\n全部处理完成！")

if __name__ == '__main__':
    update_prices()