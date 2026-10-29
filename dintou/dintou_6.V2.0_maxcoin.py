#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dintou_6.py — 多币种定投 + DOGE币本位空对冲 + DOGE网格/趋势网格 + OKX实盘交易
回测数据源: yfinance (Yahoo Finance)
实盘交易: ccxt + OKX (支持模拟盘/实盘)
交易记录: SQLite
"""

import os
import sys
import time
import math
import json
import argparse
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

# ======================== 配置区 ========================

# OKX 配置（优先从环境变量读取）
OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_SECRET_KEY = os.environ.get("OKX_SECRET_KEY", "")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")
OKX_DEMO = os.environ.get("OKX_DEMO", "true").lower() in ("true", "1", "yes")

# 数据库路径
DEFAULT_DB_PATH = "dintou_trades.db"

# 回测参数
INITIAL_CASH = 10000.0        # 初始资金 USDT
DCA_PER_BUY = 10.0            # 每次定投金额 USDT
SHORT_RATIO_1 = 0.35          # 空单比例档位1(激进版)
SHORT_RATIO_2 = 0.80          # 空单比例档位2(激进版)
SHORT_RATIO_3 = 1.50          # 空单比例档位3(激进版)
SHORT_THRESHOLD_1 = 1.08      # 价格/均价 > 1.08 开空(激进版)
SHORT_THRESHOLD_2 = 1.25      # 价格/均价 > 1.25 加仓(激进版)
SHORT_THRESHOLD_3 = 1.50      # 价格/均价 > 1.50 再加仓(激进版)
SHORT_SELL_RATIO = 0.01       # DOGE止盈卖出比例 1%(激进版)
SHORT_PROFIT_THRESHOLD = 1.50 # DOGE止盈阈值(激进版)

# 网格参数
GRID_CAPITAL = 10000.0
GRID_LOW = 0.05
GRID_HIGH = 0.50
GRID_LEVELS = 20
GRID_FEE = 0.001

# 趋势网格参数
TREND_GRID_CAPITAL = 10000.0
TREND_GRID_LOW = 0.05
TREND_GRID_HIGH = 0.50
TREND_GRID_LEVELS = 20
TREND_GRID_FEE = 0.001
TREND_WINDOW = 20
TREND_THRESHOLD = 0.02

# 实盘参数
LIVE_INTERVAL = 3600          # 定投间隔(秒)，默认1小时
LIVE_MAX_ORDER_USDT = 5000    # 单笔最大下单金额
LIVE_MIN_ORDER_USDT = 5       # 单笔最小下单金额
LIVE_RETRY = 5                # 下单重试次数

# ======================== 日志 ========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dintou6")

# ======================== 数据库 ========================

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        coin TEXT NOT NULL,
        strategy TEXT NOT NULL,
        side TEXT NOT NULL,
        inst_type TEXT NOT NULL,
        price REAL,
        qty REAL,
        amount_usdt REAL,
        pnl REAL,
        note TEXT
    );
    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        coin TEXT NOT NULL,
        strategy TEXT NOT NULL,
        spot_qty REAL,
        spot_avg REAL,
        short_qty REAL,
        short_avg REAL,
        cash REAL,
        total_equity REAL
    );
    CREATE TABLE IF NOT EXISTS run_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        level TEXT,
        message TEXT
    );
    CREATE TABLE IF NOT EXISTS run_state (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    );
    """)
    return conn

def log_trade(conn: sqlite3.Connection, coin: str, strategy: str, side: str,
              inst_type: str, price: float, qty: float, amount_usdt: float,
              pnl: float = 0.0, note: str = ""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO trades (timestamp,coin,strategy,side,inst_type,price,qty,amount_usdt,pnl,note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ts, coin, strategy, side, inst_type, price, qty, amount_usdt, pnl, note),
    )
    conn.commit()

def log_position(conn: sqlite3.Connection, coin: str, strategy: str,
                 spot_qty: float, spot_avg: float, short_qty: float,
                 short_avg: float, cash: float, total_equity: float):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO positions (timestamp,coin,strategy,spot_qty,spot_avg,short_qty,short_avg,cash,total_equity) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (ts, coin, strategy, spot_qty, spot_avg, short_qty, short_avg, cash, total_equity),
    )
    conn.commit()

def log_run(conn: sqlite3.Connection, level: str, message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO run_log (timestamp,level,message) VALUES (?,?,?)", (ts, level, message))
    conn.commit()

def save_state(conn: sqlite3.Connection, key: str, value: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT OR REPLACE INTO run_state (key,value,updated_at) VALUES (?,?,?)",
        (key, value, ts),
    )
    conn.commit()

# ======================== 数据获取 ========================

COIN_MAP = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "BNB": "BNB-USD",
    "SOL": "SOL-USD",
    "XRP": "XRP-USD",
    "DOGE": "DOGE-USD",
}

def fetch_data(coin: str, start: str = "2021-01-01", end: str = None) -> pd.DataFrame:
    import yfinance as yf
    ticker = COIN_MAP.get(coin, f"{coin}-USD")
    # 使用 Ticker.history() 比 download() 更稳定，避免 MultiIndex 列问题
    t = yf.Ticker(ticker)
    df = t.history(start=start, end=end)
    if df.empty:
        raise ValueError(f"无法获取 {coin} ({ticker}) 的数据")
    # 处理 MultiIndex 列（某些 yfinance 版本会返回 MultiIndex）
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    return df

# ======================== 回测引擎 ========================

def backtest_dca(df: pd.DataFrame, coin: str, dca_per_buy: float = DCA_PER_BUY,
                 initial_cash: float = INITIAL_CASH) -> dict:
    cash = initial_cash
    coin_qty = 0.0
    total_cost = 0.0
    trades = []
    equity_curve = []

    for i, row in df.iterrows():
        price = float(row["close"])
        if cash >= dca_per_buy and price > 0:
            buy_coin = dca_per_buy / price
            coin_qty += buy_coin
            total_cost += dca_per_buy
            cash -= dca_per_buy
        equity = cash + coin_qty * price
        equity_curve.append(equity)

    final_price = float(df.iloc[-1]["close"])
    final_equity = cash + coin_qty * final_price
    avg_entry = total_cost / coin_qty if coin_qty > 0 else 0.0
    buy_hold_equity = initial_cash / float(df.iloc[0]["close"]) * final_price
    max_dd = 0.0
    peak = equity_curve[0]
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "coin": coin, "strategy": "定投", "bars": len(df),
        "initial": initial_cash, "final_equity": final_equity,
        "pnl": final_equity - initial_cash,
        "return_pct": (final_equity - initial_cash) / initial_cash * 100,
        "buy_hold_return": (buy_hold_equity - initial_cash) / initial_cash * 100,
        "max_dd": max_dd * 100,
        "spot_coin": coin_qty, "short_coin": 0.0, "cash": cash, "final_price": final_price,
        "avg_entry": avg_entry,
    }

def backtest_short_hedge(df: pd.DataFrame, coin: str,
                         dca_per_buy: float = DCA_PER_BUY,
                         initial_cash: float = INITIAL_CASH,
                         sell_ratio: float = SHORT_SELL_RATIO,
                         profit_threshold: float = SHORT_PROFIT_THRESHOLD,
                         ratio1: float = SHORT_RATIO_1,
                         ratio2: float = SHORT_RATIO_2,
                         ratio3: float = SHORT_RATIO_3,
                         thr1: float = SHORT_THRESHOLD_1,
                         thr2: float = SHORT_THRESHOLD_2,
                         thr3: float = SHORT_THRESHOLD_3) -> dict:
    cash = initial_cash
    spot_coin = 0.0
    spot_avg = 0.0
    total_cost = 0.0
    short_coin = 0.0
    short_avg = 0.0
    short_margin = 0.0
    equity_curve = []

    for i, row in df.iterrows():
        price = float(row["close"])
        # 定投买入
        if cash >= dca_per_buy and price > 0:
            qty = dca_per_buy / price
            total_cost += dca_per_buy
            new_total = spot_avg * spot_coin + dca_per_buy
            spot_coin += qty
            spot_avg = new_total / spot_coin if spot_coin > 0 else 0
            cash -= dca_per_buy

        # 止盈卖出
        if spot_coin > 0 and short_coin > 0 and short_avg > 0:
            profit_ratio = short_avg / price if price > 0 else 0
            if profit_ratio >= profit_threshold:
                sell_qty = spot_coin * sell_ratio
                if sell_qty > 0:
                    spot_coin -= sell_qty
                    cash += sell_qty * price

        # 开空逻辑
        if spot_coin > 0 and spot_avg > 0 and price > 0:
            ratio = price / spot_avg
            target_ratio = 0
            if ratio >= thr3:
                target_ratio = ratio3
            elif ratio >= thr2:
                target_ratio = ratio2
            elif ratio >= thr1:
                target_ratio = ratio1
            if target_ratio > 0:
                target_short = spot_coin * target_ratio
                if target_short > short_coin:
                    add_qty = target_short - short_coin
                    add_margin = add_qty * price
                    if cash >= add_margin:
                        new_total = short_avg * short_coin + add_margin
                        short_coin += add_qty
                        short_avg = new_total / short_coin if short_coin > 0 else 0
                        short_margin += add_margin
                        cash -= add_margin

        # 平空逻辑
        if short_coin > 0 and short_avg > 0 and price > 0:
            ratio = price / spot_avg if spot_avg > 0 else 999
            if ratio <= 1.0:
                pnl = (short_avg - price) * short_coin
                cash += short_margin + pnl
                short_coin = 0.0
                short_avg = 0.0
                short_margin = 0.0

        equity = cash + spot_coin * price + short_margin + (short_avg - price) * short_coin
        equity_curve.append(equity)

    final_price = float(df.iloc[-1]["close"])
    final_equity = cash + spot_coin * final_price + short_margin + (short_avg - final_price) * short_coin
    buy_hold_equity = initial_cash / float(df.iloc[0]["close"]) * final_price
    max_dd = 0.0
    peak = equity_curve[0]
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "coin": coin, "strategy": f"{coin}_币本位空对冲", "bars": len(df),
        "initial": initial_cash, "final_equity": final_equity,
        "pnl": final_equity - initial_cash,
        "return_pct": (final_equity - initial_cash) / initial_cash * 100,
        "buy_hold_return": (buy_hold_equity - initial_cash) / initial_cash * 100,
        "max_dd": max_dd * 100,
        "spot_coin": spot_coin, "short_coin": short_coin, "cash": cash, "final_price": final_price,
        "avg_entry": spot_avg,
    }

def backtest_grid(df: pd.DataFrame, coin: str,
                  capital: float = GRID_CAPITAL,
                  low: float = GRID_LOW, high: float = GRID_HIGH,
                  levels: int = GRID_LEVELS, fee: float = GRID_FEE) -> dict:
    cash = capital
    coin_qty = 0.0
    step = (high - low) / levels if levels > 0 else 0
    grid_prices = [low + i * step for i in range(levels + 1)]
    equity_curve = []

    for i, row in df.iterrows():
        price = float(row["close"])
        if price <= 0:
            equity_curve.append(cash + coin_qty * price)
            continue
        for gp in grid_prices:
            if abs(price - gp) / gp < 0.001:
                if cash >= price:
                    buy_qty = cash * 0.1 / price
                    coin_qty += buy_qty * (1 - fee)
                    cash -= buy_qty
                elif coin_qty > 0:
                    sell_qty = coin_qty * 0.1
                    coin_qty -= sell_qty
                    cash += sell_qty * price * (1 - fee)
        equity_curve.append(cash + coin_qty * price)

    final_price = float(df.iloc[-1]["close"])
    final_equity = cash + coin_qty * final_price
    max_dd = 0.0
    peak = equity_curve[0] if equity_curve else capital
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "coin": coin, "strategy": f"{coin}_网格", "bars": len(df),
        "initial": capital, "final_equity": final_equity,
        "pnl": final_equity - capital,
        "return_pct": (final_equity - capital) / capital * 100,
        "buy_hold_return": 0, "max_dd": max_dd * 100,
        "spot_coin": coin_qty, "short_coin": 0.0, "cash": cash,
        "avg_entry": 0,
    }

def backtest_trend_grid(df: pd.DataFrame, coin: str,
                        capital: float = TREND_GRID_CAPITAL,
                        low: float = TREND_GRID_LOW, high: float = TREND_GRID_HIGH,
                        levels: int = TREND_GRID_LEVELS, fee: float = TREND_GRID_FEE,
                        window: int = TREND_WINDOW, threshold: float = TREND_THRESHOLD) -> dict:
    cash = capital
    coin_qty = 0.0
    step = (high - low) / levels if levels > 0 else 0
    grid_prices = [low + i * step for i in range(levels + 1)]
    closes = df["close"].values.astype(float).tolist()
    equity_curve = []

    for i, row in df.iterrows():
        price = float(row["close"])
        idx = df.index.get_loc(i)
        if idx >= window:
            ma = sum(closes[idx - window:idx]) / window
            trend_up = price > ma * (1 + threshold)
            trend_down = price < ma * (1 - threshold)
        else:
            trend_up = False
            trend_down = False

        if price <= 0:
            equity_curve.append(cash + coin_qty * price)
            continue

        if trend_up and cash >= price:
            buy_qty = cash * 0.05 / price
            coin_qty += buy_qty * (1 - fee)
            cash -= buy_qty
        elif trend_down and coin_qty > 0:
            sell_qty = coin_qty * 0.05
            coin_qty -= sell_qty
            cash += sell_qty * price * (1 - fee)
        else:
            for gp in grid_prices:
                if abs(price - gp) / gp < 0.001:
                    if cash >= price:
                        buy_qty = cash * 0.05 / price
                        coin_qty += buy_qty * (1 - fee)
                        cash -= buy_qty
                    elif coin_qty > 0:
                        sell_qty = coin_qty * 0.05
                        coin_qty -= sell_qty
                        cash += sell_qty * price * (1 - fee)

        equity_curve.append(cash + coin_qty * price)

    final_price = float(df.iloc[-1]["close"])
    final_equity = cash + coin_qty * final_price
    max_dd = 0.0
    peak = equity_curve[0] if equity_curve else capital
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "coin": coin, "strategy": f"{coin}_趋势网格", "bars": len(df),
        "initial": capital, "final_equity": final_equity,
        "pnl": final_equity - capital,
        "return_pct": (final_equity - capital) / capital * 100,
        "buy_hold_return": 0, "max_dd": max_dd * 100,
        "spot_coin": coin_qty, "short_coin": 0.0, "cash": cash,
        "avg_entry": 0,
    }

# ======================== OKX 实盘交易 ========================

def get_exchange(demo: bool = OKX_DEMO):
    """获取 ccxt OKX exchange 实例"""
    try:
        import ccxt
    except ImportError:
        raise ImportError("请安装 ccxt: pip install ccxt")

    if not OKX_API_KEY or not OKX_SECRET_KEY or not OKX_PASSPHRASE:
        raise ValueError("请设置环境变量 OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE")

    exchange = ccxt.okx({
        "apiKey": OKX_API_KEY,
        "secret": OKX_SECRET_KEY,
        "password": OKX_PASSPHRASE,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    if demo:
        exchange.set_sandbox_mode(True)
        logger.info("使用 OKX 模拟盘")
    else:
        logger.warning("使用 OKX 实盘！请确认已充分测试！")
    return exchange

def get_spot_balance(exchange, coin: str) -> Tuple[float, float]:
    """获取现货余额，返回 (USDT余额, 币种余额)"""
    try:
        balance = exchange.fetch_balance()
        usdt = balance.get("USDT", {}).get("free", 0) or 0
        coin_bal = balance.get(coin, {}).get("free", 0) or 0
        return float(usdt), float(coin_bal)
    except Exception as e:
        logger.error(f"获取余额失败: {e}")
        return 0.0, 0.0

def get_futures_position(exchange, coin: str) -> Tuple[float, float]:
    """获取永续合约持仓，返回 (持仓数量, 开仓均价)"""
    try:
        import ccxt
        inst_id = f"{coin}-USDT-SWAP"
        positions = exchange.fetch_positions([inst_id])
        for pos in positions:
            if pos.get("symbol") == inst_id:
                qty = float(pos.get("contracts", 0) or 0)
                avg = float(pos.get("entryPrice", 0) or 0)
                side = pos.get("side", "")
                if side == "short":
                    return qty, avg
                elif side == "long":
                    return -qty, avg
        return 0.0, 0.0
    except Exception as e:
        logger.error(f"获取合约持仓失败: {e}")
        return 0.0, 0.0

def place_spot_order(exchange, coin: str, side: str, amount_usdt: float,
                     retry: int = LIVE_RETRY) -> Optional[dict]:
    """现货市价单，side='buy' or 'sell'"""
    symbol = f"{coin}/USDT"
    for attempt in range(retry):
        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker["last"]
            if price is None or price <= 0:
                raise ValueError(f"{symbol} 价格异常: {price}")
            qty = amount_usdt / price
            # 检查最小下单量
            market = exchange.market(symbol)
            limits = market.get("limits", {})
            min_amount = limits.get("amount", {}).get("min", 0) or 0
            min_cost = limits.get("cost", {}).get("min", 0) or 0
            if min_amount > 0 and qty < min_amount:
                logger.warning(f"{symbol} 数量 {qty} < 最小 {min_amount}，跳过")
                return None
            if min_cost > 0 and amount_usdt < min_cost:
                logger.warning(f"{symbol} 金额 {amount_usdt} < 最小 {min_cost}，跳过")
                return None
            order = exchange.create_market_order(symbol, side, qty)
            logger.info(f"现货{side.upper()} {symbol} qty={qty} price={price}")
            return order
        except Exception as e:
            logger.warning(f"现货下单失败 (尝试{attempt+1}/{retry}): {e}")
            if attempt < retry - 1:
                time.sleep(2)
            else:
                raise
    return None

def place_swap_order(exchange, coin: str, side: str, qty: float,
                     retry: int = LIVE_RETRY) -> Optional[dict]:
    """永续合约市价单，side='open_short'/'close_short'/'open_long'/'close_long'"""
    symbol = f"{coin}/USDT:USDT"
    pos_side = "short" if "short" in side else "long"
    reduce_only = "close" in side
    for attempt in range(retry):
        try:
            params = {"posSide": pos_side, "reduceOnly": reduce_only}
            order_side = "buy" if "close_short" in side or "open_long" in side else "sell"
            order = exchange.create_market_order(symbol, order_side, qty, params=params)
            logger.info(f"合约{side} {symbol} qty={qty}")
            return order
        except Exception as e:
            logger.warning(f"合约下单失败 (尝试{attempt+1}/{retry}): {e}")
            if attempt < retry - 1:
                time.sleep(2)
            else:
                raise
    return None

def live_dca(exchange, conn: sqlite3.Connection, coin: str,
             amount_usdt: float = DCA_PER_BUY):
    """实盘定投：市价买入现货"""
    try:
        usdt_bal, coin_bal = get_spot_balance(exchange, coin)
        if usdt_bal < LIVE_MIN_ORDER_USDT:
            logger.warning(f"USDT余额不足: {usdt_bal}")
            log_run(conn, "WARN", f"{coin} 定投跳过: USDT余额不足 {usdt_bal}")
            return
        actual = min(amount_usdt, usdt_bal, LIVE_MAX_ORDER_USDT)
        if actual < LIVE_MIN_ORDER_USDT:
            logger.warning(f"定投金额 {actual} < 最小 {LIVE_MIN_ORDER_USDT}，跳过")
            return
        order = place_spot_order(exchange, coin, "buy", actual)
        if order:
            price = order.get("average") or order.get("price")
            qty = order.get("filled") or order.get("amount", 0)
            log_trade(conn, coin, "dca", "buy", "spot", price, qty, actual, note="定投买入")
            # 更新持仓快照
            _, new_coin_bal = get_spot_balance(exchange, coin)
            log_position(conn, coin, "dca", new_coin_bal, 0, 0, 0, usdt_bal - actual,
                         (usdt_bal - actual) + new_coin_bal * (price or 0))
            log_run(conn, "INFO", f"{coin} 定投买入 {qty} @ {price}, 花费 {actual} USDT")
    except Exception as e:
        logger.error(f"{coin} 定投异常: {e}")
        log_run(conn, "ERROR", f"{coin} 定投异常: {e}")

def live_short_hedge(exchange, conn: sqlite3.Connection, coin: str,
                     dca_per_buy: float = DCA_PER_BUY,
                     sell_ratio: float = SHORT_SELL_RATIO,
                     profit_threshold: float = SHORT_PROFIT_THRESHOLD,
                     ratio1: float = SHORT_RATIO_1,
                     ratio2: float = SHORT_RATIO_2,
                     ratio3: float = SHORT_RATIO_3,
                     thr1: float = SHORT_THRESHOLD_1,
                     thr2: float = SHORT_THRESHOLD_2,
                     thr3: float = SHORT_THRESHOLD_3):
    """实盘DOGE币本位空对冲"""
    try:
        # 1. 定投买入现货
        usdt_bal, spot_qty = get_spot_balance(exchange, coin)
        if usdt_bal >= LIVE_MIN_ORDER_USDT:
            actual = min(dca_per_buy, usdt_bal, LIVE_MAX_ORDER_USDT)
            if actual >= LIVE_MIN_ORDER_USDT:
                order = place_spot_order(exchange, coin, "buy", actual)
                if order:
                    price = order.get("average") or order.get("price")
                    qty = order.get("filled") or order.get("amount", 0)
                    log_trade(conn, coin, "short_hedge", "buy_spot", "spot",
                              price, qty, actual, note="定投买入")

        # 2. 获取最新状态
        usdt_bal, spot_qty = get_spot_balance(exchange, coin)
        short_qty, short_avg = get_futures_position(exchange, coin)
        ticker = exchange.fetch_ticker(f"{coin}/USDT")
        current_price = ticker["last"]

        if spot_qty <= 0 or current_price <= 0:
            return

        # 计算现货均价（简化：用当前价近似，实际应从DB读取历史均价）
        spot_avg = current_price  # 简化处理

        # 3. 止盈卖出
        if short_qty > 0 and short_avg > 0:
            profit_ratio = short_avg / current_price if current_price > 0 else 0
            if profit_ratio >= profit_threshold and spot_qty > 0:
                sell_qty = spot_qty * sell_ratio
                if sell_qty > 0:
                    order = place_spot_order(exchange, coin, "sell", sell_qty * current_price)
                    if order:
                        filled_qty = order.get("filled") or order.get("amount", 0)
                        filled_price = order.get("average") or order.get("price")
                        log_trade(conn, coin, "short_hedge", "sell_spot", "spot",
                                  filled_price, filled_qty, filled_qty * filled_price,
                                  note="止盈卖出")

        # 4. 开空逻辑
        ratio = current_price / spot_avg if spot_avg > 0 else 999
        target_ratio = 0
        if ratio >= thr3:
            target_ratio = ratio3
        elif ratio >= thr2:
            target_ratio = ratio2
        elif ratio >= thr1:
            target_ratio = ratio1

        if target_ratio > 0 and spot_qty > 0:
            target_short = spot_qty * target_ratio
            if target_short > short_qty:
                add_qty = target_short - short_qty
                margin_needed = add_qty * current_price
                if usdt_bal >= margin_needed:
                    place_swap_order(exchange, coin, "open_short", add_qty)
                    log_trade(conn, coin, "short_hedge", "open_short", "swap",
                              current_price, add_qty, margin_needed, note="开空")

        # 5. 平空逻辑
        if short_qty > 0 and ratio <= 1.0:
            place_swap_order(exchange, coin, "close_short", short_qty)
            log_trade(conn, coin, "short_hedge", "close_short", "swap",
                      current_price, short_qty, 0, note="平空")

        # 6. 记录持仓快照
        usdt_bal2, spot_qty2 = get_spot_balance(exchange, coin)
        short_qty2, short_avg2 = get_futures_position(exchange, coin)
        total_equity = usdt_bal2 + spot_qty2 * current_price + (short_avg2 - current_price) * short_qty2
        log_position(conn, coin, "short_hedge", spot_qty2, spot_avg,
                     short_qty2, short_avg2, usdt_bal2, total_equity)

    except Exception as e:
        logger.error(f"{coin} 空对冲异常: {e}")
        log_run(conn, "ERROR", f"{coin} 空对冲异常: {e}")

# ======================== 主程序 ========================

def run_backtest(coins: List[str], strategies: List[str]):
    """运行回测"""
    logger.info(f"开始回测 | 币种: {coins} | 策略: {strategies}")
    results = []
    for coin in coins:
        try:
            df = fetch_data(coin)
            logger.info(f"{coin} 数据: {len(df)} 条, 范围 {df.index[0]} ~ {df.index[-1]}")
        except Exception as e:
            logger.error(f"获取 {coin} 数据失败: {e}")
            continue

        for s in strategies:
            try:
                if s == "dca":
                    r = backtest_dca(df, coin)
                elif s == "short":
                    r = backtest_short_hedge(df, coin)
                elif s == "grid":
                    r = backtest_grid(df, coin)
                elif s == "trend_grid":
                    r = backtest_trend_grid(df, coin)
                else:
                    logger.warning(f"未知策略: {s}")
                    continue
                results.append(r)
                logger.info(f"  {r['strategy']}: 收益 {r['return_pct']:+.2f}%, "
                            f"最大回撤 {r['max_dd']:.2f}%, 最终权益 {r['final_equity']:.2f}")
            except Exception as e:
                logger.error(f"  {coin}/{s} 回测失败: {e}")

    if results:
        # 主表格 - 增加"持仓币"列
        print("\n" + "=" * 110)
        print(f"{'币种':<6} {'策略':<18} {'收益':>8} {'回撤':>8} {'最终权益':>10} {'初始':>10} {'持仓币':>14}")
        print("-" * 110)
        for r in results:
            spot = r.get('spot_coin', 0)
            short = r.get('short_coin', 0)
            fp = r.get('final_price', 0)
            # 格式化持仓显示
            if short > 0:
                coin_str = f"{spot:.2f}现货+{short:.2f}空"
            elif spot > 0:
                coin_str = f"{spot:.2f}"
            else:
                coin_str = "0"
            print(f"{r['coin']:<6} {r['strategy']:<18} {r['return_pct']:>+8.2f}% "
                  f"{r['max_dd']:>8.2f}% {r['final_equity']:>10.2f} {r['initial']:>10.2f} {coin_str:>14}")
        print("=" * 110)

        # 详细持仓信息
        print("\n=== 详细持仓 ===")
        for r in results:
            coin = r['coin']
            strategy = r['strategy']
            spot = r.get('spot_coin', 0)
            short = r.get('short_coin', 0)
            cash = r.get('cash', 0)
            fp = r.get('final_price', 0)
            avg = r.get('avg_entry', 0)
            
            print(f"\n{coin} / {strategy}:")
            print(f"  现货持仓: {spot:.4f} {coin}  (均价 {avg:.4f})")
            print(f"  空单仓位: {short:.4f} {coin}")
            print(f"  USDT余额: {cash:.2f}")
            if fp > 0:
                spot_value = spot * fp
                print(f"  现货价值: {spot_value:.2f} USDT (@ {fp:.4f})")
                total_value = cash + spot_value
                print(f"  总价值(币+现金): {total_value:.2f} USDT")
            if short > 0:
                print(f"  [提示] 当前有空单持仓 {short:.4f} {coin}，平空后会获得 USDT")
        print()
    return results

def run_live(coins: List[str], strategies: List[str], demo: bool = True,
             db_path: str = DEFAULT_DB_PATH, once: bool = False):
    """运行实盘"""
    logger.info(f"启动实盘 | 币种: {coins} | 策略: {strategies} | {'模拟盘' if demo else '实盘'}")
    exchange = get_exchange(demo)
    conn = init_db(db_path)
    log_run(conn, "INFO", f"启动 | coins={coins} strategies={strategies} demo={demo}")

    try:
        while True:
            for coin in coins:
                for s in strategies:
                    if s == "dca":
                        live_dca(exchange, conn, coin)
                    elif s == "short":
                        live_short_hedge(exchange, conn, coin)
                    elif s == "grid":
                        logger.warning(f"网格策略实盘暂未实现: {coin}")
                    elif s == "trend_grid":
                        logger.warning(f"趋势网格实盘暂未实现: {coin}")
            save_state(conn, "last_run", datetime.now().isoformat())
            if once:
                logger.info("--once 模式，执行一次后退出")
                break
            logger.info(f"等待 {LIVE_INTERVAL} 秒后下一轮...")
            time.sleep(LIVE_INTERVAL)
    except KeyboardInterrupt:
        logger.info("用户中断，退出")
    finally:
        conn.close()
        logger.info("数据库已关闭")

def main():
    parser = argparse.ArgumentParser(description="dintou_6 - 多币种定投+空对冲+网格 OKX实盘")
    parser.add_argument("--mode", choices=["backtest", "live"], default="backtest",
                        help="运行模式: backtest=回测, live=实盘")
    parser.add_argument("--coins", nargs="+", default=["DOGE"],
                        help="交易币种，如 BTC ETH DOGE")
    parser.add_argument("--strategy", nargs="+", default=["dca"],
                        choices=["dca", "short", "grid", "trend_grid"],
                        help="策略选择")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite数据库路径")
    parser.add_argument("--demo", action="store_true", default=True,
                        help="使用模拟盘（默认开启）")
    parser.add_argument("--no-demo", action="store_true",
                        help="关闭模拟盘，使用实盘")
    parser.add_argument("--once", action="store_true",
                        help="实盘模式下只执行一次后退出（调试用）")

    args = parser.parse_args()
    demo = args.demo and not args.no_demo

    if args.mode == "backtest":
        run_backtest(args.coins, args.strategy)
    elif args.mode == "live":
        run_live(args.coins, args.strategy, demo=demo, db_path=args.db, once=args.once)

if __name__ == "__main__":
    main()
