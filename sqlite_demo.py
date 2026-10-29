import sqlite3

# 1. 连接数据库，不存在则自动创建 stock.db
conn = sqlite3.connect("stock.db")
cursor = conn.cursor()

# ========== 【1.建表】==========
# 股票行情表
create_sql = """
CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,       -- 股票代码
    name TEXT,       -- 股票名称
    price REAL,      -- 当前价格
    date TEXT        -- 日期
)
"""
cursor.execute(create_sql)
conn.commit()
print("✅ 建表完成")

# ========== 【2.新增 insert】==========
def add_stock(code, name, price, date):
    sql = "INSERT INTO stock(code, name, price, date) VALUES (?, ?, ?, ?)"
    cursor.execute(sql, (code, name, price, date))
    conn.commit()
    print(f"✅ 新增成功：{name}")

# 插入两条测试数据
add_stock("000001", "平安银行", 10.25, "2026-10-10")
add_stock("600036", "招商银行", 32.10, "2026-10-10")

# ========== 【3.查询 select】==========
def query_all():
    cursor.execute("SELECT * FROM stock")
    rows = cursor.fetchall()  # fetchall() 拿到全部结果
    print("\n📋 查询全部数据：")
    for row in rows:
        print(row)
    return rows

def query_by_code(code):
    sql = "SELECT * FROM stock WHERE code = ?"
    cursor.execute(sql, (code,))
    row = cursor.fetchone() # fetchone() 获取单条
    print(f"\n🔍 根据代码查询：{row}")
    return row

query_all()
query_by_code("000001")

# ========== 【4.修改 update】==========
def update_price(code, new_price):
    sql = "UPDATE stock SET price = ? WHERE code = ?"
    cursor.execute(sql, (new_price, code))
    conn.commit()
    print(f"\n✏️ 修改完成，影响行数：{cursor.rowcount}")

update_price("000001", 10.30)
query_all()

# ========== 【5.删除 delete】==========
def del_stock(code):
    sql = "DELETE FROM stock WHERE code = ?"
    cursor.execute(sql, (code,))
    conn.commit()
    print(f"\n🗑️ 删除完成，影响行数：{cursor.rowcount}")

# del_stock("600036")
# query_all()

# 关闭
cursor.close()
conn.close()
