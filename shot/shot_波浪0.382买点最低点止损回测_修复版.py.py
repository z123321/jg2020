#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""波浪理论硬规则回测框架(买点0.382, 止损=1浪最低点L)
==================================================
把主观波浪锁死成可证伪的硬规则:
  1. 取最近 WAVE_LOOKBACK(默认300)根 K 线的最低点 L 与最高点 H
  2. 方向确认: H 出现在 L 之后(先涨后整) -> 只做多;
                L 出现在 H 之后(先跌后弹) -> 只做空
  3. 回调位: 多头 zone = H - (H-L)*FIB_BUY ; 空头对称
  4. 入场确认: 价格先跌进回调位下方, 再出现一根收盘重新站回回调位上方的确认K线才开多(空头对称)
  5. 止损: 多头跌破1浪最低点L(斐波那契0位/FIB_STOP=1.0, 跌破说明并非2浪回调)即平; 空头对称涨破最高点H
  6. 止盈: 3浪目标 = 入场价 + 1浪长度(H-L) 等长外推; 空头对称
  7. 同一滚动窗口只允许1次入场 + 平仓后 COOLDOWN_BARS 冷却, 杜绝鞭打

数据源: 默认模拟数据(离线验证逻辑); USE_MOCK=False 走真实 OKX 永续。
依赖: pip install ccxt pandas numpy
"""
import pandas as pd
import numpy as np
import logging
import os
import sys
import time

# ============================================================
# 配置区
# ============================================================
SYMBOL = "BTC/USDT"      # OKX USDT 永续合约
TIMEFRAME = "1h"              # 周期(强烈建议先切到 1h 再谈策略, 见文末提醒)
LIMIT = 2000                  # 目标 K 线根数
BATCH_SIZE = 1000
# --- 波浪硬规则参数 ---
WAVE_LOOKBACK = 300           # 滚动窗口: 找最近 N 根的高低点
FIB_BUY = 0.382               # 2浪回调买入位(按你要求固定 0.382)
FIB_STOP = 1.0                # 止损=1浪起点(斐波那契0位): 多头跌破最低点L即止损(跌破说明并非2浪), 空头对称涨破最高点H
TP_USE_IMPULSE = True         # True=3浪止盈用1浪等长外推; False=用固定盈亏比
TP_RR = 2.0                   # TP_USE_IMPULSE=False 时固定盈亏比(止盈=止损幅度*RR)

# --- 风控/成本 ---
COOLDOWN_BARS = 20            # 平仓后冷却 N 根 K 线内不开新仓
FEE_RATE = 0.0005             # 单边手续费+滑点(0.05%), 开平各扣一次
ONE_TRADE_PER_SWING = True    # 同一方向波段(同一段L-H)只允许入场1次

# --- 数据源切换 ---
USE_MOCK = False               # True=模拟数据, False=真实API

# --- 模拟数据参数 ---
MOCK_BASE_PRICE = 80000
MOCK_VOLATILITY = 0.004
MOCK_TREND_STRENGTH = 0.0006
MOCK_N_BARS = 3000

# --- 真实API配置(USE_MOCK=False时生效) ---
PROXY = "http://127.0.0.1:7890"   # 按你 Clash 实际端口改

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ============================================================
# 模拟数据生成器(含趋势/回调/反转, 用于离线验证逻辑)
# ============================================================
def generate_mock_data(n_bars=MOCK_N_BARS, base_price=MOCK_BASE_PRICE,
                       volatility=MOCK_VOLATILITY, trend=MOCK_TREND_STRENGTH):
    np.random.seed(7)
    prices = [base_price]
    cur_trend, dur = 0, 0
    for _ in range(n_bars - 1):
        if dur <= 0:
            cur_trend = np.random.choice([-1, 1], p=[0.45, 0.55])
            dur = np.random.randint(40, 120)
        dur -= 1
        ret = cur_trend * trend + np.random.normal(0, volatility)
        prices.append(prices[-1] * (1 + ret))
    prices = np.array(prices)
    np.random.seed(11)
    opens = prices[:-1]
    closes = prices[1:]
    highs = np.maximum(opens, closes) + np.abs(np.random.normal(0, volatility * prices[:-1] * 0.3, len(opens)))
    lows = np.minimum(opens, closes) - np.abs(np.random.normal(0, volatility * prices[:-1] * 0.3, len(opens)))
    volumes = np.random.uniform(100, 500, len(opens))
    start = pd.Timestamp("2026-10-01 00:00:00", tz="UTC")
    times = pd.date_range(start=start, periods=len(opens), freq="5min")
    df = pd.DataFrame({"time": times, "open": opens, "high": highs,
                       "low": lows, "close": closes, "volume": volumes})
    df.set_index("time", inplace=True)
    return df

# ============================================================
# 真实数据获取(OKX, since 驱动翻页 -> history-candles 深度回溯)
# ============================================================
def fetch_ohlcv_paged(symbol, timeframe, total_bars, batch_size=1000):
    """用 since 驱动翻页: ccxt 只有在传入 since 时才会请求可深度回溯的
    history-candles 端点; 不带 since 走普通 candles 端点, 1h 只给最近约 1440 根,
    params[after] 在更早历史无效 -> 这就是之前卡死 1440 根的根因."""
    import ccxt
    exchange = ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
        "proxies": {"http": PROXY, "https": PROXY},
    })
    tf_ms = {"1m": 60000, "3m": 180000, "5m": 300000, "15m": 900000, "30m": 1800000,
             "1h": 3600000, "2h": 7200000, "4h": 14400000, "6h": 21600000,
             "12h": 43200000, "1d": 86400000}
    period = tf_ms.get(timeframe, 3600000)

    store = {}  # 用 ts 作 key 去重
    # 第 1 批: 不带 since, 拿最近数据(candles 端点封顶 1000/1440 根)
    first = exchange.fetch_ohlcv(symbol, timeframe, limit=min(batch_size, 300))
    if not first:
        raise Exception("无法获取数据")
    for c in first:
        store[c[0]] = c
    earliest = min(c[0] for c in first)

    rounds = (total_bars + batch_size - 1) // batch_size
    for i in range(rounds):
        since = earliest - batch_size * period  # since 往前推一整批 -> 触发 history-candles
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=batch_size)
        if not batch:
            logging.info("[%d/%d] 已无更早数据, 停止翻页。", i + 1, rounds)
            break
        for c in batch:
            store[c[0]] = c
        new_earliest = min(c[0] for c in batch)
        logging.info("[%d/%d] 已累计 %d 根, 最早 %s",
                     i + 1, rounds, len(store),
                     str(pd.to_datetime(new_earliest, unit="ms", utc=True)))
        if new_earliest >= earliest:  # 推不动了 = 到达历史上线
            break
        earliest = new_earliest
        time.sleep(exchange.rateLimit / 1000 + 0.1)

    if not store:
        raise Exception("无法获取数据")
    df = pd.DataFrame(list(store.values()),
                      columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df.tail(total_bars)
# ============================================================
# 回测引擎(波浪硬规则)
# ============================================================
def backtest(df: pd.DataFrame):
    logging.info("回测数据: %d 根K线, 时间范围: %s ~ %s",
                 len(df), str(df.index[0]), str(df.index[-1]))
    logging.info("价格范围: %.2f ~ %.2f", df["close"].min(), df["close"].max())

    close = df["close"].values
    low = df["low"].values
    high = df["high"].values
    times = df.index
    n = len(df)

    position = 0
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    trades = []
    equity = 1.0
    trade_id = 0
    last_close_idx = -10 ** 9
    last_swing_key = None

    def close_trade(i, price, reason):
        nonlocal position, entry_price, stop_price, tp_price, equity, trade_id, last_close_idx
        pnl = (price - entry_price) * position / entry_price - 2 * FEE_RATE
        equity *= 1 + pnl
        trade_id += 1
        trades.append({
            "trade_id": trade_id, "exit_time": times[i],
            "side": "long" if position == 1 else "short",
            "entry_price": entry_price, "exit_price": price,
            "pnl_rate": pnl, "reason": reason,
        })
        position = 0
        entry_price = 0.0
        stop_price = 0.0
        tp_price = 0.0
        last_close_idx = i

    for i in range(WAVE_LOOKBACK, n):
        price = close[i]
        win_lo = low[i - WAVE_LOOKBACK:i]
        win_hi = high[i - WAVE_LOOKBACK:i]
        L = win_lo.min()
        H = win_hi.max()
        idx_L = int(win_lo.argmin())
        idx_H = int(win_hi.argmax())
        swing = H - L
        if swing <= 0:
            continue
        if idx_H > idx_L:
            want = 1
        elif idx_L > idx_H:
            want = -1
        else:
            want = 0
        swing_key = (round(L, 2), round(H, 2))

        if position != 0:
            if position == 1:
                if price <= stop_price:
                    close_trade(i, price, "SL")
                    continue
                if price >= tp_price:
                    close_trade(i, price, "TP")
                    continue
            else:
                if price >= stop_price:
                    close_trade(i, price, "SL")
                    continue
                if price <= tp_price:
                    close_trade(i, price, "TP")
                    continue

        if position != 0:
            continue
        if (i - last_close_idx) < COOLDOWN_BARS:
            continue
        if ONE_TRADE_PER_SWING and swing_key == last_swing_key:
            continue

        prev = close[i - 1]
        if want == 1:
            zone = H - swing * FIB_BUY
            stop = H - swing * FIB_STOP
            if prev < zone and price >= zone:
                entry_price = price
                stop_price = stop
                impulse = swing
                risk = entry_price - stop_price
                tp_price = (entry_price + impulse) if TP_USE_IMPULSE else (entry_price + risk * TP_RR)
                position = 1
                last_swing_key = swing_key
        elif want == -1:
            zone = L + swing * FIB_BUY
            stop = L + swing * FIB_STOP
            if prev > zone and price <= zone:
                entry_price = price
                stop_price = stop
                impulse = swing
                risk = stop_price - entry_price
                tp_price = (entry_price - impulse) if TP_USE_IMPULSE else (entry_price - risk * TP_RR)
                position = -1
                last_swing_key = swing_key

    if position != 0:
        price = close[-1]
        pnl = (price - entry_price) * position / entry_price - 2 * FEE_RATE
        equity *= 1 + pnl
        trade_id += 1
        trades.append({
            "trade_id": trade_id, "exit_time": times[-1],
            "side": "long" if position == 1 else "short",
            "entry_price": entry_price, "exit_price": price,
            "pnl_rate": pnl, "reason": "final",
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        logging.info("回测期间没有产生任何交易(信号过严或行情不满足波浪结构)")
        return trades_df
    total_pnl = trades_df["pnl_rate"].sum()
    win = trades_df[trades_df["pnl_rate"] > 0]
    lose = trades_df[trades_df["pnl_rate"] <= 0]
    win_rate = len(win) / len(trades_df)
    cum = trades_df["pnl_rate"].cumsum()
    max_dd = (cum - cum.expanding().max()).min()
    logging.info("")
    logging.info("=" * 40)
    logging.info("         回测结果汇总(买点0.382/止损最低点L)")
    logging.info("=" * 40)
    logging.info("品种:       %s", SYMBOL)
    logging.info("周期:       %s", TIMEFRAME)
    logging.info("回调位:     %.3f (止损=1浪起点/最低点L, 回撤位 %.3f)", FIB_BUY, FIB_STOP)
    logging.info("K线数:      %d", len(df))
    logging.info("交易次数:   %d", len(trades_df))
    logging.info("盈利次数:   %d", len(win))
    logging.info("亏损次数:   %d", len(lose))
    logging.info("胜率:       %.1f%%", win_rate * 100)
    logging.info("累计收益:   %.4f%%", total_pnl * 100)
    logging.info("最大单笔盈利: %.4f%%", trades_df["pnl_rate"].max() * 100)
    logging.info("最大单笔亏损: %.4f%%", trades_df["pnl_rate"].min() * 100)
    logging.info("最大回撤:   %.4f%%", max_dd * 100)
    logging.info("最终权益:   %.4fx", equity)
    logging.info("=" * 40)
    logging.info("")
    logging.info("交易明细:")
    for _, row in trades_df.iterrows():
        logging.info("#%d %-5s | 入场:%10.2f -> 出场:%10.2f | 盈亏:%7.4f%% | %s",
                     row["trade_id"], row["side"], row["entry_price"],
                     row["exit_price"], row["pnl_rate"] * 100, row["reason"])
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_trades.csv")
    trades_df.to_csv(csv_path, index=False)
    logging.info("\n交易明细已保存到: %s", csv_path)
    return trades_df

# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    logging.info("=" * 50)
    logging.info("波浪理论硬规则回测框架(回撤位 0.382 博 3 浪)")
    logging.info("数据源: %s", "模拟数据" if USE_MOCK else "真实API(OKX)")
    logging.info("=" * 50)
    if USE_MOCK:
        logging.info("\n[模拟模式] 生成模拟K线...")
        df = generate_mock_data()
    else:
        try:
            df = fetch_ohlcv_paged(SYMBOL, TIMEFRAME, total_bars=7000, batch_size=1000)
        except Exception as e:
            logging.error("真实数据获取失败: %s", e)
            logging.info("提示: 检查网络/代理, 或把 USE_MOCK 改 True 先验证逻辑")
            sys.exit(1)
    trades_df = backtest(df)
    if trades_df is not None and not trades_df.empty:
        logging.info("\n回测完成!")
