#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
加密货币 OHLCV 数据采集器
支持多交易所、多交易对、多周期，增量写入 SQLite。
"""

import os
import sys
import time
import sqlite3
import logging
import pandas as pd
import ccxt
from datetime import datetime, timezone

# ===================== 配置 =====================
DB_PATH = "crypto_data.db"

PROXIES = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890",
}

DEFAULT_LIMIT = 1000
MAX_PAGES = 200
PAGE_SLEEP = 0.3
SYMBOL_SLEEP = 0.5
MAX_RETRY = 3
RETRY_BASE = 2.0

EXCHANGES = ["okx", "binance","gate","bitget","kucoin","mexc", "bybit","htx", ]

DEFAULT_SINCE = {
    "1s":  "2025-01-01T00:00:00Z",
    "1m":  "2025-01-01T00:00:00Z",
    "5m":  "2024-01-01T00:00:00Z",
    "15m": "2023-01-01T00:00:00Z",
    "30m": "2023-01-01T00:00:00Z",
    "1h":  "2022-01-01T00:00:00Z",
    "4h":  "2021-01-01T00:00:00Z",
    "1d":  "2020-01-01T00:00:00Z",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ===================== 数据库 =====================
def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            exchange  TEXT NOT NULL,
            symbol    TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts        INTEGER NOT NULL,
            open      REAL,
            high      REAL,
            low       REAL,
            close     REAL,
            volume    REAL,
            PRIMARY KEY (exchange, symbol, timeframe, ts)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_sym "
        "ON ohlcv (symbol, timeframe, ts)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS data_dictionary (
            table_name   TEXT NOT NULL,
            column_name  TEXT NOT NULL,
            description  TEXT,
            PRIMARY KEY (table_name, column_name)
        )
    """)
    conn.executemany(
        "INSERT OR IGNORE INTO data_dictionary VALUES (?, ?, ?)",
        [
            ("ohlcv", "exchange",  "交易所 ID，例如 okx、binance"),
            ("ohlcv", "symbol",    "交易对，例如 BTC/USDT"),
            ("ohlcv", "timeframe", "K线周期，例如 1m/5m/1h/1d"),
            ("ohlcv", "ts",        "K线时间戳，毫秒级"),
            ("ohlcv", "open",      "开盘价"),
            ("ohlcv", "high",      "最高价"),
            ("ohlcv", "low",       "最低价"),
            ("ohlcv", "close",     "收盘价"),
            ("ohlcv", "volume",    "成交量"),
        ],
    )
    conn.commit()
    return conn


def get_last_ts(conn, exchange, symbol, timeframe):
    row = conn.execute(
        "SELECT MAX(ts) AS ts FROM ohlcv "
        "WHERE exchange=? AND symbol=? AND timeframe=?",
        (exchange, symbol, timeframe),
    ).fetchone()
    return row["ts"] if row and row["ts"] else None


def upsert(conn, exchange, symbol, timeframe, df):
    if df.empty:
        return 0
    rows = []
    for _, r in df.iterrows():
        rows.append((
            exchange, symbol, timeframe,
            int(r["timestamp"]),
            float(r["open"]), float(r["high"]),
            float(r["low"]), float(r["close"]),
            float(r["volume"]),
        ))
    conn.executemany(
        "INSERT OR IGNORE INTO ohlcv "
        "(exchange, symbol, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


# ===================== 交易所工具 =====================
def make_exchange(exchange_id):
    cls = getattr(ccxt, exchange_id)
    return cls({
        "enableRateLimit": True,
        "timeout": 20000,
        "proxies": PROXIES if any(PROXIES.values()) else None,
    })


def _backoff(retry):
    return RETRY_BASE ** retry * 2


# ===================== 数据采集 =====================
def fetch_ccxt_incremental(symbol, timeframe, since_str=None):
    if since_str is None:
        since_str = DEFAULT_SINCE.get(timeframe, "2020-01-01T00:00:00Z")

    for eid in EXCHANGES:
        ex = make_exchange(eid)
        try:
            ex.load_markets()
            if symbol not in ex.symbols:
                logger.info(f"[{eid}] {symbol} 不存在，跳过")
                continue
        except Exception as e:
            logger.warning(f"[{eid}] load_markets 失败: {e}")
            continue

        conn = init_db()
        try:
            last_ts = get_last_ts(conn, eid, symbol, timeframe)
        finally:
            conn.close()

        if last_ts is not None:
            current_since = last_ts + 1
        else:
            current_since = ex.parse8601(since_str)

        all_candles = []
        pages = 0
        empty_count = 0

        while pages < MAX_PAGES:
            pages += 1
            for retry in range(MAX_RETRY):
                try:
                    candles = ex.fetch_ohlcv(
                        symbol, timeframe,
                        since=current_since,
                        limit=DEFAULT_LIMIT,
                    )
                    break
                except Exception as e:
                    wait = _backoff(retry)
                    logger.warning(f"[{eid}] 第{retry+1}次重试: {e}")
                    time.sleep(wait)
            else:
                logger.error(f"[{eid}] 达到最大重试，跳过")
                break

            if not candles:
                empty_count += 1
                if empty_count >= 2:
                    break
                time.sleep(PAGE_SLEEP)
                continue

            empty_count = 0
            all_candles.extend(candles)
            last_candle_ts = candles[-1][0]

            if last_candle_ts <= current_since:
                break

            current_since = last_candle_ts + 1
            time.sleep(PAGE_SLEEP)

        if not all_candles:
            logger.info(f"[{eid}] {symbol} {timeframe} 无新数据")
            continue

        df = pd.DataFrame(
            all_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values("timestamp", inplace=True)

        conn = init_db()
        try:
            n = upsert(conn, eid, symbol, timeframe, df)
            logger.info(f"[{eid}] {symbol} {timeframe} 新增 {n} 条, 共 {len(df)} 条")
            return eid, df
        finally:
            conn.close()

    return None, None


# ===================== 入口 =====================
def run(timeframe="1h", target_symbols=None, since_str=None):
    if target_symbols is None:
        target_symbols = ["BTC/USDT", "ETH/USDT", "DOGE/USDT"]

    logger.info(f"开始下载 {len(target_symbols)} 个交易对, timeframe={timeframe}")
    for i, sym in enumerate(target_symbols, 1):
        logger.info(f"[{i}/{len(target_symbols)}] {sym}")
        eid, df = fetch_ccxt_incremental(sym, timeframe, since_str=since_str)
        if eid is None:
            logger.warning(f"{sym} {timeframe} 所有交易所均失败")
        time.sleep(SYMBOL_SLEEP)


if __name__ == "__main__":
    run()