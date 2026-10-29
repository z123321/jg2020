#!/usr/bin/env python3
"""
多源加密货币 K 线增量下载器 (v2 - 国内优化版)
- 主源：OKX / Gate.io / Bybit / HTX(原火币) / Bitget / MEXC / KuCoin
- 兜底：yfinance（仅主流币现货）
- 数据库：crypto_data.db，含 exchange 来源字段
- 增量：自动从本地最大时间戳往后补
- 分页：ccxt 用 since + limit 循环拉满历史
- 限流：指数退避 + jitter 重试
- 国内优化：交易所按连通性自动排序，失败自动跳过
"""

import ccxt
import pandas as pd
import sqlite3
import time
import random
import logging
from datetime import datetime, timezone

# ===================== 验证代理是否生效=====================

ex = ccxt.okx({"proxies": PROXIES, "timeout": 10000})
print(ex.fetch_ticker("BTC/USDT:USDT"))

# ===================== 配置区 =====================
DB_PATH = "crypto_data.db"

# Clash 代理（Windows 默认端口 7890）
PROXIES = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}

# 如果你的 Clash 改过端口（比如 7897 / 9090），改成对应端口即可
# 如果 Clash 开的是 TUN 模式且系统代理已全局生效，也可以把上面两行注释掉直连

CCXT_EXCHANGES = [
    {"id": "okx",     "type": "swap", "quote": "USDT"},
    {"id": "gate",    "type": "swap", "quote": "USDT"},
    {"id": "bitget",  "type": "swap", "quote": "USDT"},
    {"id": "kucoin",  "type": "swap", "quote": "USDT"},
    {"id": "mexc",    "type": "swap", "quote": "USDT"},
    {"id": "binance", "type": "swap", "quote": "USDT"},
    {"id": "bybit",   "type": "swap", "quote": "USDT"},
    {"id": "htx",     "type": "swap", "quote": "USDT"},   # 新版 ccxt 已改名
]
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
        rows.append((
            exchange_id, symbol, timeframe, ts,
            float(r.open), float(r.high), float(r.low),
            float(r.close), float(r.volume),
        ))
    conn.executemany(
        "INSERT OR IGNORE INTO kline VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


# ===================== 交易所实例 =====================
def make_exchange(exchange_id):
    """创建交易所实例，处理 ccxt 类名兼容性"""
    # ccxt 某些版本中 id 和类名不完全一致，做映射
    cls = getattr(ccxt, exchange_id)
    return cls({
        "enableRateLimit": True,
        "timeout": 20000,
        "proxies": PROXIES if any(PROXIES.values()) else None,
    })


# ===================== 收集交易对（带重试） =====================
def collect_symbols_from_exchange(exchange_id):
    """从单个交易所收集交易对，失败返回空列表"""
    cfg = EXCHANGE_CONFIG.get(exchange_id)
    if not cfg:
        log.warning("[%s] 未配置交易所类型", exchange_id)
        return []

    market_type = cfg["type"]
    quote = cfg["quote"]

    for retry in range(MAX_RETRY):
        try:
            ex = make_exchange(exchange_id)
            markets = ex.load_markets()
            symbols = []
            for sym, info in markets.items():
                if (info.get("type") == market_type
                        and info.get("quote") == quote
                        and info.get("active")):
                    symbols.append(sym)
            log.info("[%s] 找到 %d 个 %s %s", exchange_id, len(symbols), quote, market_type)
            return symbols
        except ccxt.RateLimitExceeded:
            wait = RETRY_BASE * (2 ** retry) + random.uniform(0, 0.5)
            log.warning("[%s] 限流, %.1fs 后重试 (%d/%d)", exchange_id, wait, retry + 1, MAX_RETRY)
            time.sleep(wait)
        except Exception as e:
            log.warning("[%s] 获取交易对失败: %s (重试 %d/%d)",
                        exchange_id, e, retry + 1, MAX_RETRY)
            time.sleep(1 + retry * 0.5)

    log.error("[%s] 获取交易对失败，已重试 %d 次，跳过", exchange_id, MAX_RETRY)
    return []


def collect_all_symbols():
    """从所有交易所收集去重后的交易对"""
    seen = set()
    out = []

    for eid in EXCHANGE_LIST:
        symbols = collect_symbols_from_exchange(eid)
        new_count = 0
        for s in symbols:
            if s not in seen:
                seen.add(s)
                out.append(s)
                new_count += 1
        log.info("[%s] 新增 %d 个不重复合约", eid, new_count)

    log.info("去重后共 %d 个合约", len(out))
    return sorted(out)


# ===================== 指数退避 =====================
def _backoff(attempt):
    return RETRY_BASE * (2 ** attempt) + random.uniform(0, 0.5)


# ===================== ccxt 分页增量下载 =====================
def fetch_ccxt_incremental(symbol, timeframe):
    """对每个交易所尝试分页增量下载，返回 (exchange_id, df) 或 (None, None)"""
    for eid in EXCHANGE_LIST:
        cfg = EXCHANGE_CONFIG.get(eid)
        if not cfg:
            continue

        # 先查本地最新时间戳
        since = get_last_ts(eid, symbol, timeframe)
        cur_since = since + 1 if since is not None else None

        all_rows = []
        pages = 0

        for page in range(MAX_PAGES):
            ex = None
            ok = False
            for retry in range(MAX_RETRY):
                try:
                    ex = make_exchange(eid)
                    ohlcv = ex.fetch_ohlcv(
                        symbol, timeframe=timeframe,
                        since=cur_since, limit=DEFAULT_LIMIT)
                    ok = True
                    break
                except ccxt.RateLimitExceeded:
                    wait = _backoff(retry)
                    log.warning("  [%s] 限流, %.1fs 后重试 (%d/%d)",
                                eid, wait, retry + 1, MAX_RETRY)
                    time.sleep(wait)
                except ccxt.NetworkError as e:
                    log.warning("  [%s] 网络错误: %s (重试 %d/%d)", eid, e, retry + 1, MAX_RETRY)
                    time.sleep(_backoff(retry))
                except ccxt.ExchangeError as e:
                    log.warning("  [%s] 交易所错误: %s", eid, e)
                    break  # 非限流错误不重试，直接换下一个交易所
                except Exception as e:
                    log.warning("  [%s] 未知错误: %s", eid, e)
                    break

            if not ok or not ohlcv:
                if not ok:
                    log.warning("  [%s] 所有重试均失败，换下一个交易所", eid)
                else:
                    log.info("  [%s] 无更多数据或返回空，停止分页", eid)
                break

            all_rows.extend(ohlcv)
            last_ts = ohlcv[-1][0]

            # 防止死循环：如果当前返回的最后一根 <= 起始时间戳，说明没新数据
            if cur_since is not None and last_ts <= cur_since:
                log.info("  [%s] 未拉到新数据（last_ts=%d <= since=%d），停止", eid, last_ts, cur_since)
                break

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
import requests

session = requests.Session()
if any(PROXIES.values()):
    session.proxies.update(PROXIES)

raw = yf.download(yf_sym, session=session, **kwargs)
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

        # 先走 ccxt 交易所
        eid, df = fetch_ccxt_incremental(sym, timeframe)
        if not eid:
            # ccxt 全部失败，走 yfinance 兜底
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
