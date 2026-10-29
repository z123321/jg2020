#运行方式  python search_books.py publisher 0.5
import os
import sys
import time
import sqlite3
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

SEARCH_URL = "https://openlibrary.org/search.json"
DB_FILE = "books.db"
BLACKLIST_FILE = "blacklist.txt"
KEYWORDS_FILE = "keywords.txt"
USE_PROXY = True
PROXY_HTTP = "http://127.0.0.1:7890"
PROXY_HTTPS = "http://127.0.0.1:7890"
TEST="TEXT"
PUBLISHER_RANGES = [
    # O'Reilly
    ("O'Reilly 2000-2004", "978059600000", "978059600099"),
    ("O'Reilly 2004-2014", "978059600100", "978059600999"),
    ("O'Reilly 2014-2018", "978149190000", "978149199999"),
    ("O'Reilly 2019-2023", "978149200000", "978149209999"),
    # Packt
    ("Packt 2015-2018", "978178216000", "978178216999"),
    ("Packt 2020-2023", "978180056000", "978180056999"),
    # Manning
    ("Manning 2015-2020", "978161729000", "978161729999"),
    # Apress
    ("Apress 2014-2019", "978148420000", "978148429999"),
]


def get_session():
    session = requests.Session()
    if USE_PROXY:
        session.proxies = {
            "http": PROXY_HTTP,
            "https": PROXY_HTTPS,
        }
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def load_list(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def load_blacklist(filename=BLACKLIST_FILE):
    return load_list(filename)


def load_keywords(filename=KEYWORDS_FILE):
    return load_list(filename)


def is_safe(book, blacklist):
    title = book.get("title", "")
    author = book.get("author", "")
    text = f"{title} {author}".lower()
    for word in blacklist:
        if word.lower() in text:
            return False
    return True


def isbn13_check_digit(prefix12: str) -> str:
    if len(prefix12) != 12 or not prefix12.isdigit():
        raise ValueError("prefix12 必须是12位数字")
    total = 0
    for i, ch in enumerate(prefix12):
        total += int(ch) * (1 if i % 2 == 0 else 3)
    return str((10 - total % 10) % 10)


def generate_isbn_range(start_prefix: str, end_prefix: str):
    start = int(start_prefix)
    end = int(end_prefix)
    for num in range(start, end + 1):
        prefix12 = f"{num:012d}"
        yield prefix12 + isbn13_check_digit(prefix12)


def search_books_ol(query, limit=20):
    session = get_session()
    params = {
        "q": query,
        "limit": limit,
        "fields": "title,author_name,isbn,first_publish_year,key,cover_i,subject",
    }
    try:
        resp = session.get(SEARCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  OpenLibrary 请求失败: {e}")
        return []

    results = []
    for doc in data.get("docs", []):
        isbns = doc.get("isbn", [])
        isbn_10 = ""
        isbn_13 = ""
        for isbn in isbns:
            if isinstance(isbn, str) and len(isbn) == 10:
                isbn_10 = isbn
            elif isinstance(isbn, str) and len(isbn) == 13:
                isbn_13 = isbn
                break

        if not isbn_13 and not isbn_10:
            continue

        year_raw = doc.get("first_publish_year")
        if isinstance(year_raw, list) and year_raw:
            first_publish_year = year_raw[0]
        elif isinstance(year_raw, int):
            first_publish_year = year_raw
        else:
            first_publish_year = None

        cover_i = doc.get("cover_i")
        cover_url = ""
        if cover_i:
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg"

        subjects = doc.get("subject", [])
        if isinstance(subjects, list):
            subjects = [s for s in subjects if s]
        else:
            subjects = []

        results.append({
            "title": doc.get("title", ""),
            "author": "; ".join(doc.get("author_name", []) or []),
            "isbn_10": isbn_10,
            "isbn_13": isbn_13,
            "first_publish_year": first_publish_year,
            "openlibrary_key": doc.get("key", ""),
            "cover_url": cover_url,
            "subject": "; ".join(subjects),
        })
    return results


def fetch_book_by_isbn(isbn, session):
    url = f"https://openlibrary.org/isbn/{isbn}.json"
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    isbns = data.get("isbn_10", []) + data.get("isbn_13", [])
    isbn_10 = ""
    isbn_13 = ""
    for i in isbns:
        if isinstance(i, str) and len(i) == 10:
            isbn_10 = i
        elif isinstance(i, str) and len(i) == 13:
            isbn_13 = i

    if not isbn_13:
        isbn_13 = isbn

    title = data.get("title", "")
    authors = data.get("authors", [])
    author_names = []
    for a in authors:
        if isinstance(a, dict):
            author_names.append(a.get("name", ""))

    subjects = data.get("subjects", [])
    if isinstance(subjects, list):
        subjects = [s for s in subjects if isinstance(s, str) and s]
    else:
        subjects = []

    cover_url = ""
    if data.get("cover"):
        cover_id = data["cover"]
        if isinstance(cover_id, dict):
            cover_id = cover_id.get("value")
        if cover_id:
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"

    return {
        "title": title,
        "author": "; ".join(author_names),
        "isbn_10": isbn_10,
        "isbn_13": isbn_13,
        "first_publish_year": None,
        "openlibrary_key": data.get("key", ""),
        "cover_url": cover_url,
        "subject": "; ".join(subjects),
    }


def fetch_description(openlibrary_key, session):
    if not openlibrary_key:
        return ""
    url = f"https://openlibrary.org{openlibrary_key}.json"
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return ""

    desc = data.get("description")
    if isinstance(desc, dict):
        desc = desc.get("value", "")
    if isinstance(desc, str):
        desc = desc.strip()
        if desc:
            return desc

    if data.get("type", {}).get("key") == "/type/work" and data.get("editions"):
        editions = data["editions"]
        if isinstance(editions, list) and editions:
            first_edition = editions[0]
            if isinstance(first_edition, dict):
                ed_desc = first_edition.get("description")
                if isinstance(ed_desc, dict):
                    ed_desc = ed_desc.get("value", "")
                if isinstance(ed_desc, str) and ed_desc.strip():
                    return ed_desc.strip()

    return ""


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            author TEXT,
            isbn_10 TEXT,
            isbn_13 TEXT,
            first_publish_year INTEGER,
            openlibrary_key TEXT,
            cover_url TEXT,
            subject TEXT,
            description TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_isbn13 ON books(isbn_13)")

    for col in ("cover_url", "subject", "description"):
        try:
            cursor.execute(f"ALTER TABLE books ADD COLUMN {col} TEXT")
        except Exception:
            pass

    conn.commit()
    return conn


def insert_book(cursor, book):
    try:
        cursor.execute(
            """
            INSERT INTO books (
                title, author, isbn_10, isbn_13, first_publish_year,
                openlibrary_key, cover_url, subject
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book["title"],
                book["author"],
                book["isbn_10"],
                book["isbn_13"],
                book["first_publish_year"],
                book["openlibrary_key"],
                book["cover_url"],
                book["subject"],
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def backfill_descriptions():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    session = get_session()

    cursor.execute(
        "SELECT id, openlibrary_key, description FROM books "
        "WHERE openlibrary_key != '' AND (description IS NULL OR description = '')"
    )
    rows = cursor.fetchall()
    print(f"待补描述: {len(rows)} 条")

    updated = 0
    for book_id, key, _ in rows:
        desc = fetch_description(key, session)
        if desc:
            cursor.execute("UPDATE books SET description = ? WHERE id = ?", (desc, book_id))
            conn.commit()
            updated += 1
        time.sleep(0.5)

    print(f"补完 {updated} 条描述")
    conn.close()


def crawl_isbn_range(start_prefix, end_prefix, delay=1.0, batch_report=500):
    conn = init_db()
    cursor = conn.cursor()
    session = get_session()

    cursor.execute("SELECT isbn_13 FROM books WHERE isbn_13 != ''")
    existing_isbns = {row[0] for row in cursor.fetchall()}

    total_checked = 0
    total_new = 0
    total_skip = 0
    total_empty = 0

    for isbn in generate_isbn_range(start_prefix, end_prefix):
        total_checked += 1

        if isbn in existing_isbns:
            total_skip += 1
            continue

        book = fetch_book_by_isbn(isbn, session)
        if not book:
            total_empty += 1
            continue

        print(f"  [找到] ISBN {isbn} | {book['title']} | {book['author']}")

        if insert_book(cursor, book):
            conn.commit()
            existing_isbns.add(isbn)
            total_new += 1
        else:
            total_skip += 1

        if total_checked % batch_report == 0:
            print(
                f"  进度 {total_checked} | 新增 {total_new} | 跳过 {total_skip} | 空号 {total_empty}"
            )

        time.sleep(delay)

    print(
        f"\n扫描结束: 检查 {total_checked}, 新增 {total_new}, "
        f"跳过 {total_skip}, 空号 {total_empty}"
    )
    conn.close()


def crawl_by_keywords():
    conn = init_db()
    cursor = conn.cursor()

    keywords = load_keywords()
    blacklist = load_blacklist()

    if not keywords:
        print(f"警告: {KEYWORDS_FILE} 为空或不存在，请先添加关键词")
        conn.close()
        return

    print(f"加载关键词 {len(keywords)} 个")

    cursor.execute("SELECT isbn_13 FROM books WHERE isbn_13 != ''")
    existing_isbns = {row[0] for row in cursor.fetchall()}

    total_new = 0
    total_skip = 0
    total_block = 0

    for kw in keywords:
        print(f"搜索: {kw}")
        books = search_books_ol(kw, limit=20)

        if not books:
            print("  本轮未获取到数据，跳过")
            continue

        for book in books:
            isbn = book.get("isbn_13")
            if not isbn:
                continue

            if isbn in existing_isbns:
                total_skip += 1
                continue

            if not is_safe(book, blacklist):
                print(f"  [已拦截] {book['title']}")
                total_block += 1
                continue

            if insert_book(cursor, book):
                conn.commit()
                existing_isbns.add(isbn)
                total_new += 1
            else:
                total_skip += 1

        time.sleep(1)

    cursor.execute("SELECT COUNT(*) FROM books")
    count = cursor.fetchone()[0]

    print(f"\n完成! 新增 {total_new} 条, 跳过 {total_skip} 条, 拦截 {total_block} 条")
    print(f"数据库中共有 {count} 条书记录 (文件: {DB_FILE})")

    conn.close()

    print("\n开始补全书籍描述...")
    backfill_descriptions()


def scan_publishers(delay=1.0):
    print(f"开始出版商号段扫描, 间隔 {delay}s")
    print(f"共 {len(PUBLISHER_RANGES)} 个号段\n")
    for name, start, end in PUBLISHER_RANGES:
        print(f"{'='*50}")
        print(f"扫描: {name} ({start} ~ {end})")
        print(f"{'='*50}")
        crawl_isbn_range(start, end, delay=delay)
        print()
    print("\n所有出版商号段扫描完成！")


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python search_books.py keyword        # 关键词搜索模式")
        print("  python search_books.py isbn <start> <end> [delay]  # ISBN段扫描")
        print("  python search_books.py publisher [delay]           # 出版商号段批量扫描")
        print("  python search_books.py desc             # 仅补描述")
        print("")
        print("ISBN段扫描示例:")
        print("  python search_books.py isbn 978059600000 978059600099 1.0")
        print("")
        print("出版商批量扫描示例:")
        print("  python search_books.py publisher 0.5")
        return

    mode = sys.argv[1].lower()

    if mode == "keyword":
        crawl_by_keywords()

    elif mode == "isbn":
        if len(sys.argv) < 4:
            print("ISBN模式需要指定起止前缀，例如:")
            print("  python search_books.py isbn 978059600000 978059600099")
            return
        start = sys.argv[2]
        end = sys.argv[3]
        delay = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
        print(f"开始ISBN段扫描: {start} ~ {end}, 间隔 {delay}s")
        crawl_isbn_range(start, end, delay=delay)

    elif mode == "publisher":
        delay = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
        scan_publishers(delay=delay)

    elif mode == "desc":
        backfill_descriptions()

    else:
        print(f"未知模式: {mode}")


if __name__ == "__main__":
    main()