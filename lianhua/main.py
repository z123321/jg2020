import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import time
import threading
import os
import sys
import signal
from datetime import datetime
from fastapi.responses import FileResponse
from dotenv import load_dotenv
import ccxt
import requests
import numpy as np
from strategies.grid_strategy import GridStrategy
from strategies.ma_strategy import MAStrategy
# ===== 加载 .env 文件 =====
load_dotenv()

app = FastAPI()

# ==========================================
# 0. 信号安全退出机制 (预防数据库锁死)
# ==========================================
mock_running = False
mock_thread = None
db_connection = None

def graceful_shutdown(signum, frame):
    global mock_running, db_connection
    print("\n🛑 收到停止信号，正在安全关闭...")
    mock_running = False
    
    # 等待后台线程自然结束
    if mock_thread and mock_thread.is_alive():
        mock_thread.join(timeout=5)
    
    # 关闭数据库连接
    if db_connection is None:
        db_connection.close()
        print("🛑 数据库连接已安全关闭。")
        
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)   # Ctrl+C
signal.signal(signal.SIGTERM, graceful_shutdown)  # 被系统kill

# ==========================================
# 1. 前端页面路由
# ==========================================
@app.get("/")
def read_root():
    index_path = os.path.join("templates", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    else:
        return {"error": "找不到前端页面，请检查 templates/index.html 是否存在"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 2. 全局变量与策略状态定义
# ==========================================
LIVE_STATUS = {
    "running": False,
    "last_signal": "未启动",
    "equity": 0.0,
    "current_price": 0.0,
    "time": "-",


}
current_signal = "WAITING"
LIVE_SNAPSHOTS = []
klines_data = []

# ========== 新增：策略管理核心 ==========
from strategies.grid_strategy import GridStrategy
from strategies.ma_strategy import MAStrategy

# 策略注册表：以后加了新策略，直接在这里加一行就行
strategy_catalog = {
    "grid": GridStrategy,
    "ma": MAStrategy
}

# 默认使用网格策略
active_strategy_key = "grid"
current_strategy = GridStrategy() 
# ==========================================


# 初始化全局 CCXT 客户端
exchange = None
try:
    api_key = os.getenv("OKX_API_KEY")
    secret_key = os.getenv("OKX_SECRET_KEY")
    passphrase = os.getenv("OKX_PASSPHRASE")
    is_sim = os.getenv("OKX_IS_SIM", "false").lower() == "true"

    if api_key and secret_key and passphrase:
        exchange = ccxt.okx({
            'apiKey': api_key,
            'secret': secret_key,
            'password': passphrase,
            'enableRateLimit': True,  
            'timeout': 15000,         
            'proxies': {
                'http': 'http://127.0.0.1:7890',
                'https': 'http://127.0.0.1:7890',
            },
        })
        if is_sim:
            exchange.set_sandbox_mode(True)
        exchange.load_markets()
        print("✅ CCXT OKX 客户端初始化成功！")
    else:
        print("⚠️ 未检测到 OKX 密钥，将使用安全模拟模式")
except Exception as e:
    print(f"⚠️ CCXT OKX 客户端初始化失败: {e}")
    exchange = None

# ==========================================
# 3. 数据库核心逻辑 (SQLite)
# ==========================================
DB_FILE = 'trade_data.db'

def db_conn():
    global db_connection
    if db_connection is None:
        db_connection = sqlite3.connect(DB_FILE, check_same_thread=False)
        db_connection.execute('PRAGMA journal_mode=WAL')
    return db_connection

def init_db():
    conn = db_conn()
    c = conn.cursor()
    
    # 检查并迁移旧版表结构
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='snapshots'")
    table_exists = c.fetchone() is not None
    
    if not table_exists:
        c.execute('''
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                close REAL NOT NULL,
                equity REAL NOT NULL,
                signal TEXT NOT NULL
            )
        ''')
    else:
        c.execute("PRAGMA table_info(snapshots)")
        cols = [row[1] for row in c.fetchall()]
        if 'id' not in cols:
            c.execute('ALTER TABLE snapshots RENAME TO snapshots_old')
            c.execute('''
                CREATE TABLE snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    close REAL NOT NULL,
                    equity REAL NOT NULL,
                    signal TEXT NOT NULL
                )
            ''')
            c.execute('INSERT INTO snapshots (time, symbol, close, equity, signal) SELECT time, symbol, close, equity, signal FROM snapshots_old ORDER BY time ASC')
            c.execute('DROP TABLE snapshots_old')
            print("✅ 数据库表结构已成功迁移。")

    c.execute('CREATE INDEX IF NOT EXISTS idx_snapshot_time ON snapshots(time)')
    conn.commit()
    print("数据库初始化完毕...")

def save_snapshot_to_db(snapshot: dict):
    conn = db_conn()
    c = conn.cursor()
    c.execute('INSERT INTO snapshots (time, symbol, close, equity, signal) VALUES (?, ?, ?, ?, ?)',
              (snapshot['time'], snapshot['symbol'], snapshot['close'], snapshot['equity'], snapshot['signal']))
    conn.commit()

# ==========================================
# 4. 策略核心与状态机 (双均线策略)
# ==========================================
# 模拟盘初始资金配置
INITIAL_EQUITY = 10000.0 
FAST_WINDOW = 5   # 短期均线周期
SLOW_WINDOW = 20  # 长期均线周期

prices_history = []
equity_history = []
current_signal = "WARMUP"
btc_holding = 0.0

def get_current_equity(close_price, total_usdt, total_btc):
    """
    动态计算真实净值：
    如果 API 返回真实 USDT 余额，就用真实余额；
    如果 API 返回 0（模拟盘未领资金），则兜底使用初始本金。
    """
    usdt_equity = total_usdt if total_usdt > 0 else INITIAL_EQUITY
    equity = usdt_equity + (total_btc * close_price)
    return equity

# 在文件顶部或合适位置初始化策略（可切换）
strategy = GridStrategy()  # 想换策略就改这一行！
# strategy = MAStrategy()

def background_okx_task():
    global mock_running, prices_history, equity_history, current_signal, LIVE_STATUS, current_strategy, klines_data # 【新增】global加上 klines_data
    
    print(f"【策略引擎启动】当前策略: {current_strategy.__class__.__name__}")
    
    while mock_running:
        try:
            formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 获取最新价格
            ticker = exchange.fetch_ticker('BTC/USDT')
            if not ticker or not ticker.get('last'):
                time.sleep(3)
                continue
                
            close = float(ticker['last'])

            # 【新增】前端画图专用：生成单根 K线数据
            klines_data.append({
                "time": formatted_time,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100.0
            })
            if len(klines_data) > 120: # 只保留最近 120 根
                klines_data.pop(0)

            # 获取账户余额
            balance = exchange.fetch_balance()
            total_usdt = float((balance.get('total') or {}).get('USDT') or 0.0)
            total_btc = float((balance.get('total') or {}).get('BTC') or 0.0)

            # 调用策略计算信号
            result = current_strategy.run(close, total_usdt, total_btc)
            
            # 更新全局状态
            current_signal = result["signal"]
            LIVE_STATUS["equity"] = result["equity"]
            LIVE_STATUS["current_price"] = round(close, 2)
            LIVE_STATUS["last_signal"] = result["message"]
            LIVE_STATUS["time"] = formatted_time

            # 记录快照数据
            snapshot = {
                "time": formatted_time,
                "symbol": "BTC/USDT",
                "close": close,
                "equity": result["equity"],
                "signal": current_signal
            }
            LIVE_SNAPSHOTS.append(snapshot)
            if len(LIVE_SNAPSHOTS) > 100:
                LIVE_SNAPSHOTS.pop(0)
            save_snapshot_to_db(snapshot)


            

        except Exception as e:
            print(f"❌ 策略心跳异常: {e}")
        time.sleep(3)

# ==========================================
# 5. API 接口
# ==========================================
@app.get("/live/status")
async def get_live_status():
    return LIVE_STATUS

@app.post("/live/start")
async def start_strategy():
    global mock_thread, mock_running
    if exchange is not None:
        if not mock_running:
            mock_running = True
            mock_thread = threading.Thread(target=background_okx_task, daemon=True)
            mock_thread.start()
            LIVE_STATUS["running"] = True
            return {"code": 0, "msg": "✅ OKX 模拟盘心跳已启动"}
        else:
            return {"code": 0, "msg": "心跳已在运行中"}
    else:
        return {"code": -1, "msg": "⚠️ OKX 客户端未初始化，无法启动策略"}

@app.post("/live/stop")
async def stop_strategy():
    global mock_running
    mock_running = False
    LIVE_STATUS["running"] = False
    return {"code": 0, "msg": "策略及心跳已停止"}

@app.get("/db/snapshots")
async def get_live_snapshots(limit: int = 1000, offset: int = 0):
    conn = db_conn()
    c = conn.cursor()
    c.execute('SELECT time, symbol, close, equity, signal FROM snapshots ORDER BY id ASC LIMIT ? OFFSET ?', (limit, offset))
    rows = c.fetchall()
    data = []
    for row in rows:
        data.append({"time": row[0], "symbol": row[1], "close": row[2], "equity": row[3], "signal": row[4]})
    return data

@app.post("/live/set_strategy")
def set_strategy(data: dict):
    global active_strategy_key, current_strategy
    
    strategy_key = data.get("key")
    if strategy_key in strategy_catalog:
        active_strategy_key = strategy_key
        # 创建新策略实例（默认参数）
        current_strategy = strategy_catalog[strategy_key]()
        return {"code": 200, "message": f"已切换至【{current_strategy.name}】", "active_key": strategy_key}
    else:
        return {"code": 400, "message": "找不到该策略"}

@app.get("/live/get_strategies")
def get_strategies():
    # 返回所有可用策略，让前端能生成下拉菜单
    strategies = [{"key": k, "name": v.name} for k, v in strategy_catalog.items()]
    return {"code": 200, "data": strategies, "active_key": active_strategy_key}


if __name__ == "__main__":
    init_db()
    print("系统初始化完成，服务器正在启动 (端口 8000)...")
    uvicorn.run(app, host="127.0.0.1", port=8000)