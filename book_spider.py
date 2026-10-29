import json
import time
import random
from playwright.sync_api import sync_playwright

def safe_text(loc):
    try:
        return loc.text_content(timeout=3000).strip()
    except Exception:
        return ""

def main():
    output_file = "book_result.json"
    result_list = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0")

        test_url = "https://book.kongfz.com/23200/10355632976/"
        page.goto(test_url, timeout=30000)

        sleep_sec = 6 + random.random() * 3
        time.sleep(sleep_sec)

        

        title = safe_text(page.locator("h1"))
        price_text = safe_text(page.locator("span.now-price-text"))
        isbn_text = safe_text(page.locator(""))   #这里后面复制isbn选择器填进去
        publisher_text = safe_text(page.locator(""))

        real_item = {
            "isbn": isbn_text,
            "book_title": title,
            "publisher": publisher_text,
            "publish_year": "",
            "price": price_text,
            "currency": "CNY",
            "book_condition": "",
            "source_url": test_url
        }
        result_list.append(real_item)

        browser.close()

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result_list, f, ensure_ascii=False, indent=2)

    print(f"数据输出完成 {output_file}")

if __name__ == "__main__":
    main()
