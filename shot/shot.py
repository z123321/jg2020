#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日内短线策略回测框架
===================
数据源说明:
  - 默认使用模拟数据(无需网络),用于验证策略逻辑
  - 网络恢复后,取消注释 real_data 部分的代码即可切换到真实数据源

依赖安装:
  pip install ccxt pandas numpy

使用方式:
  1. 直接运行: python shot.py  (使用模拟数据)
  2. 切换真实数据: 将 main 函数中的 use_mock = True 改为 False
     并确保网络可访问 OKX(需代理)或更换其他可用交易所
"""

import pandas as pd
import numpy as np
import logging
import os
import sys

# ============================================================
# 配置区
# ============================================================

# --- 回测参数 ---
SYMBOL = "BTC/USDT:USDT"     # OKX USDT永续合约
TIMEFRAME = "1h"
LIMIT = 2000

FAST_MA = 9
SLOW_MA = 21
RSI_PERIOD = 14
RSI_UPPER = 60
RSI_LOWER = 40

STOP_LOSS = 0.015        # 1.5% 止损
TAKE_PROFIT = 0.03       # 3% 止盈

# --- 模拟数据参数(仅 use_mock=True 时生效) ---
MOCK_BASE_PRICE = 68000      # 模拟BTC起始价格
MOCK_VOLATILITY = 0.002      # 5分钟波动率
MOCK_TREND_STRENGTH = 0.0003 # 趋势强度
MOCK_N_BARS = 500            # 模拟K线根数

# --- 数据源切换 ---
USE_MOCK = False  # True=模拟数据, False=真实API数据

# --- 真实API配置(USE_MOCK=False时生效) ---
# OKX配置(需要代理才能访问)
OKX_API_KEY = "YOUR_API_KEY"
OKX_SECRET = "YOUR_SECRET"
OKX_PASSPHRASE = "YOUR_PASSPHRASE"

# 代理配置(如果走代理的话)
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7890

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)


# ============================================================
# 模拟数据生成器
# ============================================================

def generate_mock_data(n_bars=MOCK_N_BARS, base_price=MOCK_BASE_PRICE,
                       volatility=MOCK_VOLATILITY, trend=MOCK_TREND_STRENGTH):
    """生成模拟BTC 5分钟K线数据,包含趋势+震荡+反转等真实市场特征"""
    np.random.seed(42)

    prices = [base_price]
    current_trend = 0
    trend_duration = 0

    for i in range(n_bars - 1):
        # 每20-40根K线切换一次趋势方向
        if trend_duration <= 0:
            current_trend = np.random.choice([-1, 1], p=[0.4, 0.6])
            trend_duration = np.random.randint(20, 50)
        trend_duration -= 1

        # 收益率 = 趋势 + 随机噪声
        ret = current_trend * trend + np.random.normal(0, volatility)
        new_price = prices[-1] * (1 + ret)
        prices.append(new_price)

    prices = np.array(prices)

    # 生成OHLCV
    np.random.seed(123)
    opens = prices[:-1]
    closes = prices[1:]

    ranges = np.abs(opens - closes) + np.random.normal(
        0, volatility * prices[:-1] * 0.5, size=len(opens)
    )
    highs = np.maximum(opens, closes) + np.abs(
        np.random.normal(0, volatility * prices[:-1] * 0.3, size=len(opens))
    )
    lows = np.minimum(opens, closes) - np.abs(
        np.random.normal(0, volatility * prices[:-1] * 0.3, size=len(opens))
    )
    volumes = np.random.uniform(100, 500, size=len(opens))

    # 生成时间戳
    start_time = pd.Timestamp("2026-10-19 00:00:00", tz="UTC")
    times = pd.date_range(start=start_time, periods=len(opens), freq="5min")

    df = pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    df.set_index("time", inplace=True)
    return df


# ============================================================
# 真实数据获取
# ============================================================

import ccxt
import pandas as pd
import time

# ===== 配置 =====
SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "5m"
BATCH_SIZE = 300       # OKX 单次上限
TOTAL_BARS = 2000      # 目标K线数量，约 7 天数据
PROXY = "http://127.0.0.1:7890"  # 按你Clash实际端口改

# ===== 初始化交易所 =====
exchange = ccxt.okx({
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
    "proxies": {
        "http": PROXY,
        "https": PROXY,
    },
})

# ===== 分页拉取 =====
def fetch_ohlcv_paged(symbol, timeframe, total_bars, batch_size=300):
    # 第一步：先拉最新一批，确定起始时间
    latest = exchange.fetch_ohlcv(symbol, timeframe, limit=batch_size)
    if not latest:
        raise Exception("无法获取数据")
    all_ohlcv = latest[:]
    # 从最早那根往前翻
    since = latest[0][0] - 1

    rounds = (total_bars - batch_size + batch_size - 1) // batch_size
    for i in range(rounds):
        print(f"[{i+1}/{rounds}] 正在拉取更早的数据...")
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=batch_size)
        if not ohlcv or len(ohlcv) < 2:
            break
        all_ohlcv = ohlcv + all_ohlcv
        since = ohlcv[0][0] - 1
        time.sleep(exchange.rateLimit / 1000 + 0.1)

    df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df.tail(total_bars)

# ===== 执行 =====
df = fetch_ohlcv_paged(SYMBOL, TIMEFRAME, TOTAL_BARS, BATCH_SIZE)
print(f"\n拉取完成: {len(df)} 根K线")
print(f"时间范围: {df.index[0]} ~ {df.index[-1]}")
print(f"价格范围: {df['low'].min():.2f} ~ {df['high'].max():.2f}")

# ============================================================
# 技术指标
# ============================================================

def rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI指标计算"""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


# ============================================================
# 回测引擎
# ============================================================

def backtest(df: pd.DataFrame):
    """
    回测主引擎

    信号逻辑:
      - 快线上穿慢线 + RSI > 60 -> 开多
      - 快线下穿慢线 + RSI < 40 -> 开空

    平仓逻辑(优先级从高到低):
      1. 止损: 亏损 >= STOP_LOSS
      2. 止盈: 盈利 >= TAKE_PROFIT
      3. 日内平仓: UTC 23:55 后强制平仓
      4. 信号反向: 持多时出现空信号(或反之)
    """
    logging.info(
        "回测数据: %d 根K线, 时间范围: %s ~ %s",
        len(df), str(df.index[0]), str(df.index[-1])
    )
    logging.info("价格范围: %.2f ~ %.2f", df["close"].min(), df["close"].max())

    # 计算指标
    df = df.copy()
    df["fast_ma"] = df["close"].rolling(FAST_MA).mean()
    df["slow_ma"] = df["close"].rolling(SLOW_MA).mean()
    df["rsi"] = rsi(df["close"], RSI_PERIOD)
    df = df.dropna()

    position = 0          # 0=空仓, 1=多, -1=空
    entry_price = 0.0
    trades = []
    equity = 1.0
    trade_id = 0

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        price = curr["close"]
        bar_time = curr.name

        # 均线交叉信号
        fast_cross_up = (prev["fast_ma"] <= prev["slow_ma"] and
                        curr["fast_ma"] > curr["slow_ma"])
        fast_cross_down = (prev["fast_ma"] >= prev["slow_ma"] and
                          curr["fast_ma"] < curr["slow_ma"])

        signal = 0
        if fast_cross_up and curr["rsi"] > RSI_UPPER:
            signal = 1
        elif fast_cross_down and curr["rsi"] < RSI_LOWER:
            signal = -1

        # --- 平仓逻辑 ---

        # 1. 日内强制平仓: UTC 23:55 后不持仓
        if bar_time.hour == 23 and bar_time.minute >= 55:
            if position != 0:
                pnl = (price - entry_price) * position / entry_price
                equity *= 1 + pnl
                trade_id += 1
                trades.append({
                    "trade_id": trade_id,
                    "exit_time": bar_time,
                    "side": "long" if position == 1 else "short",
                    "entry_price": entry_price,
                    "exit_price": price,
                    "pnl_rate": pnl,
                    "reason": "EOD",
                })
                position = 0
                entry_price = 0.0
            continue  # 当日不再开新仓

        # 2. 止损止盈检查
        if position != 0 and entry_price > 0:
            pnl_rate = (price - entry_price) * position / entry_price
            if pnl_rate <= -STOP_LOSS or pnl_rate >= TAKE_PROFIT:
                equity *= 1 + pnl_rate
                trade_id += 1
                trades.append({
                    "trade_id": trade_id,
                    "exit_time": bar_time,
                    "side": "long" if position == 1 else "short",
                    "entry_price": entry_price,
                    "exit_price": price,
                    "pnl_rate": pnl_rate,
                    "reason": "SL/TP",
                })
                position = 0
                entry_price = 0.0

        # 3. 信号反向平仓+反手
        if signal != 0 and position == 0:
            position = signal
            entry_price = price
        elif signal != 0 and position != 0 and signal != position:
            # 反向信号: 先平旧仓再开新仓
            pnl = (price - entry_price) * position / entry_price
            equity *= 1 + pnl
            trade_id += 1
            trades.append({
                "trade_id": trade_id,
                "exit_time": bar_time,
                "side": "long" if position == 1 else "short",
                "entry_price": entry_price,
                "exit_price": price,
                "pnl_rate": pnl,
                "reason": "reverse",
            })
            position = signal
            entry_price = price

    # 未平仓结算
    if position != 0:
        price = df.iloc[-1]["close"]
        pnl = (price - entry_price) * position / entry_price
        equity *= 1 + pnl
        trade_id += 1
        trades.append({
            "trade_id": trade_id,
            "exit_time": df.index[-1],
            "side": "long" if position == 1 else "short",
            "entry_price": entry_price,
            "exit_price": price,
            "pnl_rate": pnl,
            "reason": "final",
        })

    # --- 统计输出 ---
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        logging.info("回测期间没有产生任何交易")
        return trades_df

    total_pnl = trades_df["pnl_rate"].sum()
    win_trades = trades_df[trades_df["pnl_rate"] > 0]
    lose_trades = trades_df[trades_df["pnl_rate"] <= 0]
    win_rate = len(win_trades) / len(trades_df) if len(trades_df) > 0 else 0
    max_pnl = trades_df["pnl_rate"].max()
    max_loss = trades_df["pnl_rate"].min()

    # 最大回撤
    cum_pnl = trades_df["pnl_rate"].cumsum()
    peak = cum_pnl.expanding().max()
    drawdown = cum_pnl - peak
    max_dd = drawdown.min()

    logging.info("")
    logging.info("=" * 40)
    logging.info("         回测结果汇总")
    logging.info("=" * 40)
    logging.info("品种:       %s", SYMBOL)
    logging.info("周期:       %s", TIMEFRAME)
    logging.info("K线数:      %d", len(df))
    logging.info("交易次数:   %d", len(trades_df))
    logging.info("盈利次数:   %d", len(win_trades))
    logging.info("亏损次数:   %d", len(lose_trades))
    logging.info("胜率:       %.1f%%", win_rate * 100)
    logging.info("累计收益:   %.4f%%", total_pnl * 100)
    logging.info("最大单笔盈利: %.4f%%", max_pnl * 100)
    logging.info("最大单笔亏损: %.4f%%", max_loss * 100)
    logging.info("最大回撤:   %.4f%%", max_dd * 100)
    logging.info("最终权益:   %.4fx", equity)
    logging.info("=" * 40)
    logging.info("")
    logging.info("交易明细:")

    for _, row in trades_df.iterrows():
        logging.info(
            "#%d %-5s | 入场:%10.2f -> 出场:%10.2f | 盈亏:%7.4f%% | %s",
            row["trade_id"],
            row["side"],
            row["entry_price"],
            row["exit_price"],
            row["pnl_rate"] * 100,
            row["reason"],
        )

    # 保存交易明细到CSV
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_trades.csv")
    trades_df.to_csv(csv_path, index=False)
    logging.info("\n交易明细已保存到: %s", csv_path)

    return trades_df


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    logging.info("=" * 50)
    logging.info("日内短线策略回测框架")
    logging.info("数据源: %s", "模拟数据" if USE_MOCK else "真实API(OKX)")
    logging.info("=" * 50)

    if USE_MOCK:
        # --- 模拟数据模式 ---
        logging.info("\n[模拟模式] 生成模拟BTC 5分钟K线数据...")
        df = generate_mock_data()
    else:
        # --- 真实数据模式 ---
        try:
            df = fetch_ohlcv_paged(SYMBOL, TIMEFRAME, LIMIT, LIMIT)
        except Exception as e:
            logging.error("真实数据获取失败: %s", e)
            logging.info("\n提示: 请检查网络连接或代理配置")
            logging.info("也可以将 USE_MOCK 改为 True 使用模拟数据测试策略逻辑")
            sys.exit(1)

    # 运行回测
    trades_df = backtest(df)

    if trades_df is not None and not trades_df.empty:
        logging.info("\n回测完成!")
