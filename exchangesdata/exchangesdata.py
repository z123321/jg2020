#!/usr/bin/env python3
"""
多源加密货币 K 线增量下载器
- 主源：OKX / Binance / Bybit / Huobi / Gate
- 兜底：yfinance（仅主流币现货）
- 数据库：crypto_data.db，含 exchange 来源字段
- 增量：自动从本地最大时间戳往后补
- 分页：ccxt 用 since + limit 循环拉满历史
- 限流：指数退避 + jitter 重试
"""

import ccxt
import pandas as pd
import sqlite3
import time
import random
import logging

# ===================== 配置区 =====================
DB_PATH = "crypto_data.db"

# clash 代理，本地跑就打开
PROXIES = {
    # "http": "http://127.0.0.1:7890",
    # "https": "http://127.0.0.1:7890",
}

# 交易所优先级，前面挂了自动切后面
CCXT_EXCHANGES = [
    {"id": "okx",     "type": "swap", "quote": "USDT"},
    {"id": "binance", "type": "swap", "quote": "USDT"},
    {"id": "bybit",   "type": "swap", "quote": "USDT"},
    {"id": "huobi",   "type": "swap", "quote": "USDT"},
    {"id": "gate",    "type": "swap", "quote": "USDT"},
]

YF_ENABLED = True          # 是否启用 yfinance 兜底
DEFAULT_LIMIT = 1000       # ccxt 单页 limit
MAX_PAGES = 200            # 单币种单交易所最大分页次数（防死循环）
PAGE_SLEEP = 0.3           # 同交易所分页间隔（秒）
SYMBOL_SLEEP = 0.8         # 每下载完一个币的间隔（秒）
MAX_RETRY = 5              # 单请求最大重试次数
RETRY_BASE = 1.5           # 指数退避基数（秒）

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ===================== 数据库 =====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS kline (
        exchange  TEXT    NOT NULL,
        symbol    TEXT    NOT NULL,
        timeframe TEXT    NOT NULL,
        timestamp INTEGER NOT NULL,
        open      REAL,
        high      REAL,
        low       REAL,
        close     REAL,
        volume    REAL,
        PRIMARY KEY (exchange, symbol, timeframe, timestamp)
    )
    """)
    conn.commit()
    conn.close()


def get_last_ts(exchange_id, symbol, timeframe):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT MAX(timestamp) FROM kline "
        "WHERE exchange=? AND symbol=? AND timeframe=?",
        (exchange_id, symbol, timeframe),
    ).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def save_klines(exchange_id, symbol, timeframe, df):
    if df is None or df.empty:
        return 0
    conn = sqlite3.connect(DB_PATH)
    rows = []
    for r in df.itertuples(index=False):
        ts = int(pd.to_datetime(r.timestamp).timestamp() * 1000)
        rows.append((exchange_id, symbol, timeframe, ts,
                     float(r.open), float(r.high), float(r.low),
                     float(r.close), float(r.volume)))
    conn.executemany(
        "INSERT OR IGNORE INTO kline VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


# ===================== 交易所实例 =====================
def make_exchange(exchange_id):
    cls = getattr(ccxt, exchange_id)
    return cls({
        "enableRateLimit": True,
        "timeout": 15000,
        "proxies": PROXIES if any(PROXIES.values()) else None,
    })


# ===================== 收集全量交易对 =====================
def collect_all_symbols():
    seen, out = set(), []
    for cfg in CCXT_EXCHANGES:
        try:
            ex = make_exchange(cfg["id"])
            markets = ex.load_markets()
            for sym, info in markets.items():
                if (info.get("type") == cfg["type"]
                        and info.get("quote") == cfg["quote"]
                        and info.get("active")
                        and sym not in seen):
                    seen.add(sym)
                    out.append(sym)
            log.info("[%s] 找到 %d 个 %s %s",
                     cfg["id"], len([s for s in out if s in seen]),
                     cfg["quote"], cfg["type"])
        except Exception as e:
            log.warning("[%s] 获取交易对失败: %s", cfg["id"], e)
    log.info("去重后共 %d 个合约", len(out))
    return sorted(out)


# ===================== 重试装饰 =====================
def _backoff(attempt):
    return RETRY_BASE * (2 ** attempt) + random.uniform(0, 0.5)


# ===================== ccxt 分页增量 =====================
def fetch_ccxt_incremental(symbol, timeframe):
    """对每个交易所尝试分页增量下载，返回 (exchange_id, df) 或 (None, None)"""
    for cfg in CCXT_EXCHANGES:
        eid = cfg["id"]
        since = get_last_ts(eid, symbol, timeframe)
        if since is not None:
            since += 1  # 从最后一条之后开始

        all_rows = []
        cur_since = since
        pages = 0

        for attempt_page in range(MAX_PAGES):
            ex = make_exchange(eid)
            ok = False
            for retry in range(MAX_RETRY):
                try:
                    ohlcv = ex.fetch_ohlcv(
                        symbol, timeframe=timeframe,
                        since=cur_since, limit=DEFAULT_LIMIT)
                    ok = True
                    break
                except ccxt.RateLimitExceeded:
                    wait = _backoff(retry)
                    log.warning("  [%s] 限流, %.1fs 后重试(%d/%d)",
                                eid, wait, retry + 1, MAX_RETRY)
                    time.sleep(wait)
                except Exception as e:
                    log.warning("  [%s] 错误: %s", eid, e)
                    break

            if not ok or not ohlcv:
                break

            all_rows.extend(ohlcv)
            last_ts = ohlcv[-1][0]
            if cur_since is not None and last_ts <= cur_since:
                break  # 没有新数据了
            cur_since = last_ts + 1
            pages += 1
            time.sleep(PAGE_SLEEP)

        if all_rows:
            df = pd.DataFrame(all_rows, columns=[
                "timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            log.info("  ✅ %s 分页 %d 页, 共 %d 条", eid, pages, len(df))
            return eid, df

    return None, None


# ===================== yfinance 兜底 =====================
def _yf_interval(tf):
    return {"1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1h", "4h": "1d", "1d": "1d"}.get(tf, "1d")


def fetch_yf_incremental(symbol, timeframe):
    if not YF_ENABLED:
        return None, None
    try:
        import yfinance as yf
    except ImportError:
        log.warning("未安装 yfinance: pip install yfinance")
        return None, None

    base = symbol.split("/")[0]
    yf_sym = f"{base}-USD"
    since = get_last_ts("yfinance", symbol, timeframe)
    interval = _yf_interval(timeframe)

    kwargs = {"interval": interval, "progress": False}
    if since:
        from datetime import datetime
        kwargs["start"] = datetime.utcfromtimestamp(
            (since + 1) / 1000).strftime("%Y-%m-%d")
    else:
        kwargs["period"] = "7d" if interval == "1h" else "max"

    for retry in range(MAX_RETRY):
        try:
            raw = yf.download(yf_sym, **kwargs)
            if raw is None or raw.empty:
                return None, None
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0] for c in raw.columns]
            df = raw.reset_index().rename(columns={
                "Datetime": "timestamp", "Date": "timestamp",
                "Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume"})
            df = df.dropna(subset=["open", "close"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            log.info("  ✅ yfinance(%s) %d 条", yf_sym, len(df))
            return "yfinance", df
        except Exception as e:
            wait = _backoff(retry)
            log.warning("  yfinance 重试(%d/%d): %s", retry + 1, MAX_RETRY, e)
            time.sleep(wait)
    return None, None


# ===================== 主流程 =====================
def run(timeframe="1h", max_symbols=50):
    init_db()
    symbols = collect_all_symbols()
    if max_symbols:
        symbols = symbols[:max_symbols]

    log.info("开始增量更新 %s，共 %d 个合约", timeframe, len(symbols))

    for idx, sym in enumerate(symbols, 1):
        log.info("[%d/%d] %s", idx, len(symbols), sym)

        eid, df = fetch_ccxt_incremental(sym, timeframe)
        if not eid:
            eid, df = fetch_yf_incremental(sym, timeframe)

        if eid:
            n = save_klines(eid, sym, timeframe, df)
            log.info("  入库 %d 条 (来源: %s)", n, eid)
        else:
            log.warning("  全部源失败，跳过")

        time.sleep(SYMBOL_SLEEP)

    log.info("增量更新完成")


if __name__ == "__main__":
    run(timeframe="1h", max_symbols=50)