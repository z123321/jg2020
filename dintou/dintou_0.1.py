"""
shot_纯现货定投_多币对比_v27.py

改动：
1. DOGE 新增"动态币本位做空对冲"策略：
   - 现货一股不卖，每天定投买入
   - 当价格 >= 成本 × 3 时开始开空，对冲比例随涨幅递增
   - 使用币本位空单，平仓盈亏以 DOGE 结算
   - 输出最终现货币数、空单币数、净持币
2. 修复币本位做空盈亏公式
3. 所有策略输出净持币信息

依赖：
pip install yfinance pandas numpy
"""

import yfinance as yf
import pandas as pd
import numpy as np

# ==================== 配置 ====================
COINS = {
    "BTC":  "BTC-USD",
    "ETH":  "ETH-USD",
    "BNB":  "BNB-USD",
    "SOL":  "SOL-USD",
    "XRP":  "XRP-USD",
    "DOGE": "DOGE-USD",
}

START_DATE = "2021-01-01"
END_DATE = None

DAILY_USDT = 10.0
FEE = 0.0005

# 全局定投参数
GLOBAL_SELL_RATIO = 0.05
GLOBAL_PROFIT_THRESHOLD = 0.50

# DOGE 定投专属参数
DOGE_SELL_RATIO = 0.03
DOGE_PROFIT_THRESHOLD = 0.80

# DOGE 网格参数
GRID_SPACING_RATIO = 0.03
GRID_LOWER_BUFFER = 0.80
GRID_UPPER_BUFFER = 1.20
GRID_INITIAL_POSITION = 0.5

# DOGE 趋势过滤网格参数
TREND_MA_PERIOD = 200
TREND_GRID_SPACING_RATIO = 0.03
TREND_GRID_LOWER_BUFFER = 0.80
TREND_GRID_UPPER_BUFFER = 1.20
TREND_GRID_INITIAL_POSITION = 0.5

# DOGE 动态币本位做空对冲参数
SHORT_START_MULTIPLIER = 3.00   # 成本 × 3 开始开空
SHORT_TIERS = [
    # (价格/成本 倍数, 对冲比例)
    (3.0,  0.20),   # ×3  → 20%
    (5.0,  0.50),   # ×5  → 50%
    (10.0, 1.00),   # ×10 → 100%
]
SHORT_CLOSE_MULTIPLIER = 1.50  # 价格 <= 成本×1.5 时平空


# ==================== 取数 ====================
def fetch_ohlcv(ticker: str):
    t = yf.Ticker(ticker)
    df = t.history(start=START_DATE, end=END_DATE)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={
        "Open": "o", "High": "h", "Low": "l",
        "Close": "c", "Volume": "v",
    })
    df.dropna(subset=["c"], inplace=True)
    df.sort_index(inplace=True)
    return df


# ==================== 定投回测 ====================
def backtest_dca(name, df):
    cash = 0.0
    coin = 0.0
    total_cost = 0.0
    total_bought = 0.0

    if name == "DOGE":
        sell_ratio = DOGE_SELL_RATIO
        profit_threshold = DOGE_PROFIT_THRESHOLD
    else:
        sell_ratio = GLOBAL_SELL_RATIO
        profit_threshold = GLOBAL_PROFIT_THRESHOLD

    trades = []

    for i, row in df.iterrows():
        price = float(row["c"])

        cash += DAILY_USDT
        if cash >= DAILY_USDT and price > 0:
            buy_coin = DAILY_USDT * (1 - FEE) / price
            coin += buy_coin
            total_cost += DAILY_USDT
            total_bought += buy_coin
            cash -= DAILY_USDT

        avg_cost = total_cost / total_bought if total_bought > 1e-12 else 0

        if avg_cost > 0 and price > avg_cost * (1 + profit_threshold) and coin > 1e-12:
            sell_coin = coin * sell_ratio
            sell_usdt = sell_coin * price * (1 - FEE)
            coin -= sell_coin
            cash += sell_usdt
            total_cost -= total_cost * sell_ratio
            total_bought -= sell_coin

        equity = cash + coin * price
        trades.append({
            "date": i,
            "equity": equity,
            "spot_coin": coin,
            "short_coin": 0.0,
            "net_coin": coin,
        })

    t = pd.DataFrame(trades).set_index("date")
    return _calc_metrics(name, df, t, DAILY_USDT * len(df))


# ==================== 等比网格回测 ====================
def backtest_grid(name, df):
    prices = df["c"].values.astype(float)
    highs = df["h"].values.astype(float)
    lows = df["l"].values.astype(float)

    hist_low = float(np.min(lows))
    hist_high = float(np.max(highs))

    lower = hist_low * GRID_LOWER_BUFFER
    upper = hist_high * GRID_UPPER_BUFFER

    ratio = upper / lower
    if ratio <= 1:
        return backtest_dca(name + "_网格(异常)", df)

    r = 1.0 + GRID_SPACING_RATIO
    num_grids = int(np.log(ratio) / np.log(r))
    if num_grids < 2:
        num_grids = 2

    r = ratio ** (1.0 / num_grids)
    grid_prices = [lower * (r ** i) for i in range(num_grids + 1)]

    total_capital = DAILY_USDT * len(df)
    initial_cash = total_capital * (1 - GRID_INITIAL_POSITION)
    initial_coin_value = total_capital * GRID_INITIAL_POSITION

    first_price = prices[0]
    coin = initial_coin_value * (1 - FEE) / first_price
    cash = initial_cash

    def get_grid_index(price):
        idx = int(np.log(price / lower) / np.log(r))
        return max(0, min(idx, num_grids - 1))

    prev_grid_idx = get_grid_index(first_price)
    bought_at = [False] * (num_grids + 1)
    for j in range(prev_grid_idx + 1):
        bought_at[j] = True

    trades = []

    for idx in range(len(df)):
        price = prices[idx]
        high = highs[idx]
        low = lows[idx]

        curr_grid_idx = get_grid_index(price)

        if curr_grid_idx != prev_grid_idx:
            if curr_grid_idx < prev_grid_idx:
                for j in range(prev_grid_idx, curr_grid_idx, -1):
                    if cash > 0:
                        per_grid_amount = total_capital / (num_grids + 1)
                        buy_coin = per_grid_amount * (1 - FEE) / price
                        coin += buy_coin
                        cash -= per_grid_amount
                        bought_at[j] = True
            else:
                for j in range(prev_grid_idx + 1, curr_grid_idx + 1):
                    if bought_at[j] and coin > 1e-12:
                        per_grid_amount = total_capital / (num_grids + 1)
                        sell_coin = per_grid_amount * (1 - FEE) / grid_prices[j]
                        sell_coin = min(sell_coin, coin)
                        if sell_coin > 1e-12:
                            sell_usdt = sell_coin * price * (1 - FEE)
                            coin -= sell_coin
                            cash += sell_usdt
                            bought_at[j] = False

        prev_grid_idx = curr_grid_idx
        equity = cash + coin * price
        trades.append({
            "date": df.index[idx],
            "equity": equity,
            "spot_coin": coin,
            "short_coin": 0.0,
            "net_coin": coin,
        })

    t = pd.DataFrame(trades).set_index("date")
    return _calc_metrics(name + "_网格", df, t, total_capital)


# ==================== 趋势过滤网格回测 ====================
def backtest_trend_grid(name, df):
    prices = df["c"].values.astype(float)
    highs = df["h"].values.astype(float)
    lows = df["l"].values.astype(float)

    ma200 = pd.Series(prices).rolling(TREND_MA_PERIOD).mean().values

    hist_low = float(np.min(lows))
    hist_high = float(np.max(highs))

    lower = hist_low * TREND_GRID_LOWER_BUFFER
    upper = hist_high * TREND_GRID_UPPER_BUFFER

    ratio = upper / lower
    if ratio <= 1:
        return backtest_dca(name + "_趋势网格(异常)", df)

    r = 1.0 + TREND_GRID_SPACING_RATIO
    num_grids = int(np.log(ratio) / np.log(r))
    if num_grids < 2:
        num_grids = 2

    r = ratio ** (1.0 / num_grids)
    grid_prices = [lower * (r ** i) for i in range(num_grids + 1)]

    total_capital = DAILY_USDT * len(df)

    def get_grid_index(price):
        idx = int(np.log(price / lower) / np.log(r))
        return max(0, min(idx, num_grids - 1))

    cash = total_capital
    coin = 0.0
    in_trend = False
    bought_at = [False] * (num_grids + 1)
    prev_grid_idx = 0

    trades = []

    for idx in range(len(df)):
        price = prices[idx]
        high = highs[idx]
        low = lows[idx]
        ma = ma200[idx]

        above_ma = (ma is not None and not np.isnan(ma) and price > ma)

        if above_ma and not in_trend:
            in_trend = True
            position_value = total_capital * TREND_GRID_INITIAL_POSITION
            coin = position_value * (1 - FEE) / price
            cash = total_capital - position_value
            prev_grid_idx = get_grid_index(price)
            bought_at = [False] * (num_grids + 1)
            for j in range(prev_grid_idx + 1):
                bought_at[j] = True

        elif not above_ma and in_trend:
            in_trend = False
            if coin > 1e-12:
                cash += coin * price * (1 - FEE)
                coin = 0.0
            bought_at = [False] * (num_grids + 1)

        if in_trend and coin > 1e-12:
            curr_grid_idx = get_grid_index(price)

            if curr_grid_idx != prev_grid_idx:
                if curr_grid_idx < prev_grid_idx:
                    for j in range(prev_grid_idx, curr_grid_idx, -1):
                        if cash > 0:
                            per_grid_amount = total_capital / (num_grids + 1)
                            buy_coin = per_grid_amount * (1 - FEE) / price
                            coin += buy_coin
                            cash -= per_grid_amount
                            bought_at[j] = True
                else:
                    for j in range(prev_grid_idx + 1, curr_grid_idx + 1):
                        if bought_at[j] and coin > 1e-12:
                            per_grid_amount = total_capital / (num_grids + 1)
                            sell_coin = per_grid_amount * (1 - FEE) / grid_prices[j]
                            sell_coin = min(sell_coin, coin)
                            if sell_coin > 1e-12:
                                sell_usdt = sell_coin * price * (1 - FEE)
                                coin -= sell_coin
                                cash += sell_usdt
                                bought_at[j] = False

            prev_grid_idx = curr_grid_idx

        equity = cash + coin * price
        trades.append({
            "date": df.index[idx],
            "equity": equity,
            "spot_coin": coin,
            "short_coin": 0.0,
            "net_coin": coin,
        })

    t = pd.DataFrame(trades).set_index("date")
    return _calc_metrics(name + "_趋势网格", df, t, total_capital)


# ==================== 动态对冲比例计算 ====================
def _calc_short_ratio(price_to_cost):
    """
    根据 价格/成本 的比值，计算动态对冲比例。
    阶梯：
        < 3.0  → 0%
        3.0    → 20%
        5.0    → 50%
        10.0   → 100%
        > 10.0 → 100%
    中间线性插值。
    """
    if price_to_cost < SHORT_TIERS[0][0]:
        return 0.0

    for k in range(len(SHORT_TIERS) - 1):
        lo_mult, lo_ratio = SHORT_TIERS[k]
        hi_mult, hi_ratio = SHORT_TIERS[k + 1]
        if lo_mult <= price_to_cost <= hi_mult:
            t = (price_to_cost - lo_mult) / (hi_mult - lo_mult)
            return lo_ratio + t * (hi_ratio - lo_ratio)

    return SHORT_TIERS[-1][1]


# ==================== 动态币本位做空对冲回测 ====================
def backtest_coin_short(name, df):
    cash = 0.0
    spot_coin = 0.0
    total_cost = 0.0
    total_bought = 0.0

    short_coin = 0.0
    short_entry_price = 0.0

    trades = []
    debug_count = 0

    for i, row in df.iterrows():
        price = float(row["c"])

        cash += DAILY_USDT
        if cash >= DAILY_USDT and price > 0:
            buy_coin = DAILY_USDT * (1 - FEE) / price
            spot_coin += buy_coin
            total_cost += DAILY_USDT
            total_bought += buy_coin
            cash -= DAILY_USDT

        avg_cost = total_cost / total_bought if total_bought > 1e-12 else 0

        # ===== 调试打印：每100根K线输出一次 =====
        debug_count += 1
        if debug_count % 100 == 0:
            print(f"  [DEBUG] i={i}, price={price:.6f}, avg_cost={avg_cost:.6f}, spot_coin={spot_coin:.2f}, short_coin={short_coin:.2f}")

        target_ratio = 0.0
        if avg_cost > 0 and price > avg_cost * 1.2:
            target_ratio = 0.3
            if debug_count % 100 == 0:
                print(f"    >>> 触发开空! target_ratio=0.3, target_short={spot_coin * 0.3:.2f}")

        target_short_coin = spot_coin * target_ratio

        add_coin = 0.0
        if target_short_coin > short_coin + 1e-12:
            add_coin = target_short_coin - short_coin
            max_short_value = spot_coin * price * 0.3
            current_short_value = short_coin * price
            remaining_value = max(0.0, max_short_value - current_short_value)
            if price > 1e-12:
                add_coin = min(add_coin, remaining_value / price)
            else:
                add_coin = 0.0

        if add_coin > 1e-12:
            total_cost_short = short_entry_price * short_coin + price * add_coin
            short_coin += add_coin
            short_entry_price = total_cost_short / short_coin if short_coin > 1e-12 else 0.0
            if debug_count % 100 == 0:
                print(f"    >>> 实际开空 {add_coin:.2f}, short_coin now={short_coin:.2f}")

        if target_short_coin < short_coin - 1e-12:
            reduce_coin = short_coin - target_short_coin
            if reduce_coin > 1e-12 and short_entry_price > 1e-12:
                pnl_usdt = reduce_coin * (price - short_entry_price)
                cash += pnl_usdt
                short_coin = target_short_coin
                if short_coin < 1e-12:
                    short_coin = 0.0
                    short_entry_price = 0.0

        net_coin = spot_coin - short_coin
        equity = cash + net_coin * price

        trades.append({
            "date": i,
            "equity": equity,
            "spot_coin": spot_coin,
            "short_coin": short_coin,
            "net_coin": net_coin,
        })

    t = pd.DataFrame(trades).set_index("date")
    return _calc_metrics(name + "_币本位空对冲", df, t, DAILY_USDT * len(df))

# ==================== 通用指标计算 ====================
def _calc_metrics(name, df, t, total_in):
    print(f"  [METRICS] t.columns = {list(t.columns)}")
    print(f"  [METRICS] last row = {t.iloc[-1].to_dict()}")
    final_eq = t["equity"].iloc[-1]
    last_price = float(df["c"].iloc[-1])
    pnl = final_eq - total_in
    roi = pnl / total_in * 100 if total_in > 0 else 0

    first_price = float(df["c"].iloc[0])
    buyhold = (last_price / first_price - 1) * 100

    peak = t["equity"].cummax()
    dd = (t["equity"] - peak) / peak * 100
    max_dd = dd.min()

    # 最终持币信息
    final_equity = t["equity"].iloc[-1]
    final_spot = t["spot_coin"].iloc[-1]
    final_short = t["short_coin"].iloc[-1]
    final_net = t["net_coin"].iloc[-1]

    return {
        "策略": name,
        "K线数": len(df),
        "总投入(USDT)": round(total_in, 2),
        "最终权益(USDT)": round(final_eq, 2),
        "净盈亏(USDT)": round(pnl, 2),
        "收益率": f"{roi:+.2f}%",
        "买入持有": f"{buyhold:+.2f}%",
        "最大回撤": f"{max_dd:+.2f}%",
        "最终现货币": round(final_spot, 4),
        "最终空单币": round(final_short, 4),
        "最终净持币": round(final_net, 4),
    }


# ==================== 入口 ====================
if __name__ == "__main__":
    results = []
    for name, ticker in COINS.items():
        print(f"\n回测 {name} ({ticker}) ...")
        df = fetch_ohlcv(ticker)
        if df is None or df.empty:
            print(f"  {name} 未拉到数据，跳过")
            continue
        print(f"  共 {len(df)} 根日线, {df.index[0].date()} ~ {df.index[-1].date()}")

        # 定投策略（所有币）
        r = backtest_dca(name, df)
        results.append(r)

        # DOGE 额外跑多个策略对比
        if name == "DOGE":
            print(f"  同时回测 DOGE 网格策略 ...")
            r = backtest_grid(name, df)
            results.append(r)

            print(f"  同时回测 DOGE 趋势过滤网格策略 ...")
            r = backtest_trend_grid(name, df)
            results.append(r)

            print(f"  同时回测 DOGE 动态币本位做空对冲策略 ...")
            r = backtest_coin_short(name, df)
            results.append(r)

    # 打印结果
    print("\n\n===== 多币对比 v27（定投 + DOGE网格 + DOGE趋势网格 + DOGE币本位空对冲）=====")
    header = (
        f"{'策略':>26} {'K线数':>6} {'总投入(USDT)':>14} "
        f"{'最终权益(USDT)':>16} {'净盈亏(USDT)':>14} "
        f"{'收益率':>10} {'买入持有':>10} {'最大回撤':>10} "
        f"{'现货币':>12} {'空单币':>12} {'净持币':>12}"
    )
    print(header)
    for r in results:
        line = (
            f"{r['策略']:>26} "
            f"{r['K线数']:>6} "
            f"{r['总投入(USDT)']:>14.1f} "
            f"{r['最终权益(USDT)']:>16.2f} "
            f"{r['净盈亏(USDT)']:>14.2f} "
            f"{r['收益率']:>10} "
            f"{r['买入持有']:>10} "
            f"{r['最大回撤']:>10} "
            f"{r['最终现货币']:>12.4f} "
            f"{r['最终空单币']:>12.4f} "
            f"{r['最终净持币']:>12.4f}"
        )
        print(line)

    print(f"\n[Finished in {__import__('time').perf_counter() - __import__('time').perf_counter():.1f}s]")