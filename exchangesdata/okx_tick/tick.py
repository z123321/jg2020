import time
import json
import threading
import sqlite3
import sys
from collections import deque
import websocket
import requests

# ===================== 配置 =====================
HEAT_LOW = 0.3
HEAT_HIGH = 0.7
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7890
BOOK_INTERVAL = 0.8
VOL_MA_WINDOW = 10
FILTER_RATIO = 0.2
SNAPSHOT_INTERVAL = 3
RETENTION_DAY = 7
DB_FILE = "binance_heat.db"
# ===============================================

class TradeHeatMonitor:
    def __init__(self, heat_win, vol_ma_win):
        self.heat_window = heat_win
        self.vol_ma_window = vol_ma_win
        self.heat_records = deque()
        self.seen_ids = set()
        self.vol_queue = deque()
        self.bid1 = 0.0
        self.ask1 = 0.0
        self.last_alert = None
        self.last_price = 0.0

    def set_book(self, bid1, ask1):
        self.bid1 = bid1
        self.ask1 = ask1

    def set_last_price(self, p):
        self.last_price = p

    def _prune(self, now_ms):
        while self.heat_records and self.heat_records[0][1] < (now_ms - self.heat_window * 1000):
            old_id, _, _, _, _ = self.heat_records.popleft()
            self.seen_ids.discard(old_id)
        while self.vol_queue and self.vol_queue[0][0] < (now_ms - self.vol_ma_window * 1000):
            self.vol_queue.popleft()

    def add_trade(self, trade_id, ts, is_taker_buy, is_taker_sell, amount):
        if trade_id in self.seen_ids:
            return False
        now_ms = time.time() * 1000
        self._prune(now_ms)
        vol_avg = 0
        if len(self.vol_queue) > 0:
            vol_avg = sum(v[1] for v in self.vol_queue) / len(self.vol_queue)
        if vol_avg > 0 and amount < vol_avg * FILTER_RATIO:
            return False
        self.seen_ids.add(trade_id)
        self.heat_records.append((trade_id, ts, is_taker_buy, is_taker_sell, amount))
        self.vol_queue.append((ts, amount))
        return True

    def calc(self):
        buy_vol = 0.0
        sell_vol = 0.0
        for _, _, tb, ts, amt in self.heat_records:
            if tb:
                buy_vol += amt
            if ts:
                sell_vol += amt
        total = buy_vol + sell_vol
        heat = buy_vol / total if total > 0 else 0.5
        vol_avg = 0
        if len(self.vol_queue) > 0:
            vol_avg = sum(v[1] for v in self.vol_queue) / len(self.vol_queue)
        return heat, buy_vol, sell_vol, vol_avg

    def get_alert(self, heat):
        alert = None
        if heat <= HEAT_LOW:
            alert = "🔴【告警】热度下穿阈值，空头增强"
        elif heat >= HEAT_HIGH:
            alert = "🟢【告警】热度上穿阈值，多头增强"
        if alert == self.last_alert:
            return None
        if alert is not None:
            self.last_alert = alert
            return alert
        self.last_alert = None
        return None


def init_db(symbol):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    table_name = f"snap_{symbol.lower()}"
    cur.execute(f'''
    CREATE TABLE IF NOT EXISTS {table_name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER,
        price REAL,
        heat REAL,
        buy_vol REAL,
        sell_vol REAL,
        vol_avg REAL,
        bid1 REAL,
        ask1 REAL
    )
    ''')
    conn.commit()
    conn.close()

def db_snapshot_worker(symbol, monitor):
    init_db(symbol)
    table_name = f"snap_{symbol.lower()}"
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    while True:
        time.sleep(SNAPSHOT_INTERVAL)
        heat, buyV, sellV, vol_avg = monitor.calc()
        ts = int(time.time())
        p = monitor.last_price
        b1 = monitor.bid1
        a1 = monitor.ask1
        cur.execute(f'''
            INSERT INTO {table_name}(ts, price, heat, buy_vol, sell_vol, vol_avg, bid1, ask1)
            VALUES (?,?,?,?,?,?,?,?)
        ''', (ts, p, heat, buyV, sellV, vol_avg, b1, a1))
        cutoff = int(time.time()) - RETENTION_DAY * 86400
        cur.execute(f"DELETE FROM {table_name} WHERE ts < ?", (cutoff,))
        conn.commit()

def fetch_book(symbol, monitor):
    proxies = {
        "http": f"http://{PROXY_HOST}:{PROXY_PORT}",
        "https": f"http://{PROXY_HOST}:{PROXY_PORT}"
    }
    while True:
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={symbol.upper()}&limit=1"
            r = requests.get(url, proxies=proxies, timeout=4)
            res = r.json()
            bid1 = float(res["bids"][0][0])
            ask1 = float(res["asks"][0][0])
            monitor.set_book(bid1, ask1)
        except Exception:
            pass
        time.sleep(BOOK_INTERVAL)

def run_ws(symbol, monitor):
    ws_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@trade"
    def on_message(ws, message):
        d = json.loads(message)
        tid = d["t"]
        ts = d["T"]
        price = float(d["p"])
        qty = float(d["q"])
        m = d["m"]
        monitor.set_last_price(price)
        is_taker_buy = not m
        is_taker_sell = m
        is_new = monitor.add_trade(tid, ts, is_taker_buy, is_taker_sell, qty)
        if not is_new:
            return
        heat, buyV, sellV, vol_avg = monitor.calc()
        side_tag = "🟢主动买" if is_taker_buy else "🔴主动卖"
        alert_msg = monitor.get_alert(heat)
        base_line = (f"\r{symbol.upper()} | {side_tag} | 🔥热度:{heat:.3f} | 主动买:{buyV:.4f} | 主动卖:{sellV:.4f} "
                     f"| 现价:{price:.6f} | B1:{monitor.bid1:.6f} A1:{monitor.ask1:.6f} | 单均量:{vol_avg:.2f}")
        print(base_line, end="")
        if alert_msg:
            print("\n" + alert_msg)
    def on_error(ws, error):
        print(f"\nWS error: {error}")
    def on_close(ws, close_code, close_msg):
        print(f"\n⚠️ {symbol.upper()} WS断开，3秒后自动重连...")
        time.sleep(3)
        run_ws(symbol, monitor)
    def on_open(ws):
        print(f"\n✅ 币安现货WS已连接 | {symbol.upper()} | 热度窗口{WINDOW_SEC}s | 成交量均线{VOL_MA_WINDOW}s")
        print(f"💾 快照入库间隔 {SNAPSHOT_INTERVAL}s，自动保留 {RETENTION_DAY} 天数据\n")
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever(
        proxy_type="http",
        http_proxy_host=PROXY_HOST,
        http_proxy_port=PROXY_PORT,
        ping_interval=20,
        ping_timeout=10
    )

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python tick_main.py dogeusdt")
        print("示例：python tick_main.py btcusdt")
        sys.exit(0)
    SYMBOL = sys.argv[1].lower()
    WINDOW_SEC = 5
    monitor = TradeHeatMonitor(WINDOW_SEC, VOL_MA_WINDOW)
    threading.Thread(target=fetch_book, daemon=True, args=(SYMBOL, monitor)).start()
    threading.Thread(target=db_snapshot_worker, daemon=True, args=(SYMBOL, monitor)).start()
    run_ws(SYMBOL, monitor)
