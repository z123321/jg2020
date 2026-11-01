# debug_page.py - 诊断 AbeBooks 页面结构
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import time
import os

# 获取脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))

# 启动浏览器
options = uc.ChromeOptions()
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("--headless=new")  # 无头模式
driver = uc.Chrome(options=options)

# 测试多个 ISBN
test_isbns = ["9781890774974", "9781943872749"]

for isbn in test_isbns:
    url = f"https://www.abebooks.com/servlet/SearchResults?kn={isbn}"
    print(f"\n{'='*60}")
    print(f"正在打开: {url}")
    print(f"{'='*60}")
    
    try:
        driver.get(url)
        time.sleep(6)  # 等待页面完全加载
        
        # 保存完整 HTML
        html = driver.page_source
        html_file = os.path.join(script_dir, f"page_debug_{isbn}.html")
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n[OK] 完整 HTML 已保存到: {html_file}")
        print(f"[OK] HTML 长度: {len(html)} 字符")
        
        # 打印页面标题
        print(f"\n[标题] {driver.title}")
        
        # 打印 body 文本（前3000字符）
        body_text = driver.find_element(By.TAG_NAME, 'body').text
        print(f"\n=== Body 文本(前3000字符) ===")
        print(body_text[:3000])
        
        # 查找所有包含价格相关的文本
        print(f"\n=== 包含 '$' 的文本元素 ===")
        dollar_elements = driver.find_elements(By.XPATH, "//*[contains(text(), '$')]")
        for i, el in enumerate(dollar_elements[:20]):
            print(f"  [{i}] {el.text}")
        
        # 查找所有包含 'price' 的文本
        print(f"\n=== 包含 'price' 的文本元素 ===")
        price_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'price') or contains(text(), 'Price')]")
        for i, el in enumerate(price_elements[:10]):
            print(f"  [{i}] {el.text}")
        
        # 查找所有 div 的 class 名（前50个）
        print(f"\n=== 所有 div 的 class 属性(前50个) ===")
        divs = driver.find_elements(By.TAG_NAME, 'div')
        classes = set()
        for div in divs[:100]:
            cls = div.get_attribute('class')
            if cls:
                classes.add(cls)
        for c in sorted(classes)[:50]:
            print(f"  class: {c}")
        
        # 查找所有包含 'result' 的 class
        print(f"\n=== 包含 'result' 的 class ===")
        result_classes = [c for c in classes if 'result' in c.lower()]
        for c in result_classes:
            print(f"  {c}")
            
    except Exception as e:
        print(f"\n[错误] {e}")

driver.quit()
print("\n[完成] 浏览器已关闭")
print("请查看生成的 page_debug_*.html 文件，并将其发给我分析")
