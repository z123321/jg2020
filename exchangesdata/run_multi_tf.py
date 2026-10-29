#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多周期批量采集入口
默认：全量 USDT 现货 + 大周期（1d 1w 1M 1y）
支持 --symbols 指定币种，--timeframes 指定周期
"""
# 默认全量 + 大周期
# python run_multi_tf.py

# 只跑 BTC 和 ETH 的 1m 周期
# python run_multi_tf.py --symbols BTC,ETH --timeframes 1m

# 只跑 SOL 的所有大周期
#python run_multi_tf.py --symbols SOL

# 跑指定几个币 + 指定几个周期
#python run_multi_tf.py --symbols BTC,ETH,SOL --timeframes 1h,4h,1d

import sys
import sqlite3
import ccxt
from datetime import datetime, timezone
from collector import run, DB_PATH

# ===================== 自动获取交易对 =====================
def get_usdt_symbols(limit=None):
    PROXIES = {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    ex = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "proxies": PROXIES if any(PROXIES.values()) else None,
    })
    ex.load_markets()
    symbols = sorted([
        s for s, m in ex.markets.items()
        if m.get("quote") == "USDT"
        and m.get("active", False)
        and m.get("type") == "spot"
    ])
    if limit and limit > 0:
        return symbols[:limit]
    return symbols


# ===================== 预检查 =====================
MAX_GAP_MINUTES = {
    "1m": 5, "5m": 15, "15m": 30, "30m": 60,
    "1h": 120, "4h": 480, "1d": 2880,
    "1w": 10080, "1M": 43200, "1y": 525600,
}

def is_complete(symbol, timeframe):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        row = conn.execute(
            "SELECT MAX(ts) AS ts FROM ohlcv WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
        conn.close()
        if row is None or row["ts"] is None:
            return False
        last_time = datetime.fromtimestamp(row["ts"] / 1000, tz=timezone.utc)
        gap = (datetime.now(timezone.utc) - last_time).total_seconds() / 60
        max_gap = MAX_GAP_MINUTES.get(timeframe, 120)
        return gap <= max_gap
    except Exception:
        return False


# ===================== 解析命令行参数 =====================
def parse_args():
    symbols_arg = None
    timeframes_arg = None
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--symbols" and i + 1 < len(sys.argv):
            symbols_arg = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--timeframes" and i + 1 < len(sys.argv):
            timeframes_arg = sys.argv[i + 1]
            i += 2
        else:
            i += 1
    return symbols_arg, timeframes_arg


# ===================== 入口 =====================
if __name__ == "__main__":
    symbols_arg, timeframes_arg = parse_args()

    # 解析币种
    if symbols_arg:
        raw = [s.strip() for s in symbols_arg.split(",") if s.strip()]
        SYMBOLS = [s if "/" in s else s + "/USDT" for s in raw]
    else:
        SYMBOLS = get_usdt_symbols()

    # 解析周期
    if timeframes_arg:
        TIMEFRAMES = [tf.strip() for tf in timeframes_arg.split(",") if tf.strip()]
    else:
        TIMEFRAMES = ["1d", "1w", "1M", "1y"]

    print(f"本次采集 {len(SYMBOLS)} 个交易对: {SYMBOLS}")
    print(f"周期: {TIMEFRAMES}")

    for tf in TIMEFRAMES:
        print(f"\n{'='*50}")
        print(f"  开始跑 {tf} 周期")
        print(f"{'='*50}")

        need_pull = []
        for sym in SYMBOLS:
            if is_complete(sym, tf):
                print(f"  [跳过] {sym} {tf} 已是最新")
            else:
                need_pull.append(sym)

        if not need_pull:
            print(f"  {tf} 周期全部完整，无需采集")
            continue

        print(f"  需要采集: {need_pull}")
        run(timeframe=tf, target_symbols=need_pull)