import requests
from bs4 import BeautifulSoup
import time

url = "https://www.gushiwen.cn/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def crawl_and_save():
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, "html.parser")
        # 取外层容器，一组诗词放在一个 sons 里面
        son_blocks = soup.select("div[class*='sons']")

        with open("poems.txt", "a", encoding="utf-8") as f:
            f.write("===== 抓取时间：" + time.strftime("%Y-%m-%d %H:%M:%S") + " =====\n")
            count = 0
            # 最多取4组诗词
            for block in son_blocks[:4]:
                a_tags = block.select("a")
                if len(a_tags) == 0:
                    continue
                title_tag = a_tags[0]
                poem_title = title_tag.get_text(strip=True)
                poem_link = title_tag.get("href")
                if not poem_title or not poem_link:
                    continue

                # 拼接完整url
                if poem_link.startswith("/"):
                    poem_link = "https://www.gushiwen.cn" + poem_link

                # 访问详情页拿诗文正文
                detail_resp = requests.get(poem_link, headers=headers, timeout=10)
                detail_resp.encoding = 'utf-8'
                detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                content_tag = detail_soup.select_one("div[class*='contson']")
                poem_content = content_tag.get_text(strip=True, separator="\n") if content_tag else "无正文"

                # 写入文件
                f.write(f"【{poem_title}】\n")
                f.write(poem_content + "\n\n")
                print(f"✅{poem_title}")
                count += 1
                time.sleep(2.5) # 详情页访问间隔，防风控
            f.write("\n")
        print(f"✅本轮抓取{count}首完整诗词，等待下一轮...\n")

    except Exception as e:
        print(f"❌本轮出错：{e}，等待下一轮\n")


if __name__ == "__main__":
    print("爬虫启动，每60秒执行一轮，按 Ctrl+C 停止！")
    while True:
        crawl_and_save()
        time.sleep(60)
