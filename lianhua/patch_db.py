import sqlite3
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 假设你原来的 app 实例已经初始化好了，如果这里报错，说明你的main.py里不是叫 app
# 如果你的 main.py 里是 app = FastAPI()，请确保它在这段代码之前已经运行。
# 如果这里运行报错，说明我们需要把这部分代码合并到 main.py 的尾部。

# 1. 初始化 SQLite 数据库和表结构
def init_db():
    conn = sqlite3.connect('trade_data.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            close REAL NOT NULL,
            equity REAL NOT NULL,
            signal TEXT NOT NULL
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_snapshot_time ON snapshots(time)')
    conn.commit()
    conn.close()

# 2. 辅助函数：保存快照到数据库
def save_to_db(snapshot: dict):
    LIVE_SNAPSHOTS.append(snapshot)
    conn = sqlite3.connect('trade_data.db')
    c = conn.cursor()
    c.execute('INSERT INTO snapshots (time, symbol, close, equity, signal) VALUES (?, ?, ?, ?, ?)',
              (snapshot['time'], snapshot['symbol'], snapshot['close'], snapshot['equity'], snapshot['signal']))
    conn.commit()
    conn.close()

# 3. 供前端调用的分页接口
# (这个函数需要注册到你原来的 app 路由里)
@app.get("/db/snapshots")
async def get_live_snapshots(limit: int = 200, offset: int = 0):
    conn = sqlite3.connect('trade_data.db')
    c = conn.cursor()
    c.execute('SELECT * FROM snapshots ORDER BY time DESC LIMIT ? OFFSET ?', (limit, offset))
    rows = c.fetchall()
    conn.close()
    
    data = []
    for row in rows:
        data.append({
            "id": row[0], "time": row[1], "symbol": row[2],
            "close": row[3], "equity": row[4], "signal": row[5]
        })
    return data