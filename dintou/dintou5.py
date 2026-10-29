# -*- coding: utf-8 -*-
"""
dintou_5.py
基于 dintou_4 的多币种定投 + 币本位空对冲回测
参数：空单比例 25% / 60% / 120%，现货止盈保留
"""

import os
import time
import pandas as pd
import numpy as np

# ============================================================
# 1. 这里保留你原本的数据加载方式
#    例如：从 BaoStock、CSV、本地数据库或 OKX/CCXT 拉取日线数据
# ============================================================

COINS = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE"]
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


def load_coin_data(coin: str) -> pd.DataFrame:
    """
    请替换成你本地的真实数据加载逻辑。
    返回 DataFrame，至少包含列：
        date, open, high, low, close
    日期按升序排列。
    """



# ============================================================
# 2. 策略参数
# ============================================================

INITIAL_USDT = 20_000
DAILY_BUY_USDT = 100

# 币本位空对冲参数（dintou_4）
SHORT_OPEN_MULT = 2.0      # 价格 / 持仓均价 >= 2.0 才考虑开空
SHORT_CLOSE_MULT = 1.5     # 价格 / 持仓均价 <= 1.5 时平仓
SHORT_RATIO_2X = 0.25      # 价格/成本 ≈ 2 倍时的对冲比例
SHORT_RATIO_4X = 0.60      # 价格/成本 ≈ 4 倍时的对冲比例
SHORT_RATIO_8X = 1.20      # 价格/成本 ≈ 8 倍时的对冲比例

# 现货止盈参数
SPOT_PROFIT_RATIO = {
    "DOGE": (0.03, 1.80),
}
DEFAULT_SPOT_SELL_RATIO = 0.05
DEFAULT_SPOT_PROFIT_MULT = 1.50


# ============================================================
# 3. 单币种币本位空对冲回测
# ============================================================

def backtest_coin_short(df: pd.DataFrame, coin: str):
    prices = df["close"].values.astype(float)
    n = len(prices)

    spot_coin = 0.0
    cash = INITIAL_USDT
    avg_cost = 0.0

    short_coin = 0.0
    short_entry_price = 0.0

    sell_ratio, profit_mult = SPOT_PROFIT_RATIO.get(
        coin, (DEFAULT_SPOT_SELL_RATIO, DEFAULT_SPOT_PROFIT_MULT)
    )

    equity_list = []
    spot_list = []
    cash_list = []
    short_coin_list = []
    short_pnl_list = []
    price_list = []

    for i in range(n):
        price = prices[i]
        if price <= 0:
            equity_list.append(cash + spot_coin * price)
            spot_list.append(spot_coin)
            cash_list.append(cash)
            short_coin_list.append(short_coin)
            short_pnl_list.append(0.0)
            price_list.append(price)
            continue

        # 1) 定投买入现货
        if cash >= DAILY_BUY_USDT:
            buy_coin = DAILY_BUY_USDT / price
            total_cost = avg_cost * spot_coin + DAILY_BUY_USDT
            spot_coin += buy_coin
            avg_cost = total_cost / spot_coin if spot_coin > 1e-12 else 0.0
            cash -= DAILY_BUY_USDT

        # 2) 现货止盈
        if spot_coin > 1e-12 and avg_cost > 1e-12 and price >= avg_cost * profit_mult:
            sell_coin = spot_coin * sell_ratio
            if sell_coin > 1e-12:
                cash += sell_coin * price
                spot_coin -= sell_coin
                if spot_coin < 1e-12:
                    spot_coin = 0.0
                    avg_cost = 0.0
                else:
                    avg_cost = avg_cost  # 卖出均价不变

        # 3) 币本位空单逻辑
        target_short_coin = 0.0
        if spot_coin > 1e-12 and avg_cost > 1e-12:
            mult = price / avg_cost
            if mult >= 8.0:
                target_short_coin = spot_coin * SHORT_RATIO_8X
            elif mult >= 4.0:
                ratio = SHORT_RATIO_4X + (SHORT_RATIO_8X - SHORT_RATIO_4X) * (mult - 4.0) / 4.0
                target_short_coin = spot_coin * ratio
            elif mult >= 2.0:
                ratio = SHORT_RATIO_2X + (SHORT_RATIO_4X - SHORT_RATIO_2X) * (mult - 2.0) / 2.0
                target_short_coin = spot_coin * ratio

        # 开空
        if target_short_coin > short_coin + 1e-12:
            add_coin = target_short_coin - short_coin
            if short_coin < 1e-12:
                short_entry_price = price
            else:
                total_cost_short = short_entry_price * short_coin + price * add_coin
                short_coin += add_coin
                short_entry_price = total_cost_short / short_coin if short_coin > 1e-12 else price

            short_coin = target_short_coin

        # 平仓
        if target_short_coin < short_coin - 1e-12:
            reduce_coin = short_coin - target_short_coin
            if reduce_coin > 1e-12 and short_entry_price > 1e-12:
                pnl_doge = reduce_coin * (short_entry_price - price) / price
                spot_coin += pnl_doge
                if spot_coin < 0:
                    cash += spot_coin * price
                    spot_coin = 0.0

            short_coin = target_short_coin
            if short_coin < 1e-12:
                short_coin = 0.0
                short_entry_price = 0.0

        # 4) 计算权益
        short_pnl = short_coin * (short_entry_price - price) if short_coin > 1e-12 and short_entry_price > 1e-12 else 0.0
        equity = cash + spot_coin * price + short_pnl

        equity_list.append(equity)
        spot_list.append(spot_coin)
        cash_list.append(cash)
        short_coin_list.append(short_coin)
        short_pnl_list.append(short_pnl)
        price_list.append(price)

    equity_series = pd.Series(equity_list)
    peak = equity_series.cummax()
    drawdown = (equity_series - peak) / peak
    max_dd = drawdown.min()

    final_equity = equity_series.iloc[-1]
    final_spot = spot_list[-1]
    final_cash = cash_list[-1]
    final_short = short_coin_list[-1]
    total_return = (final_equity / INITIAL_USDT - 1) * 100

    return {
        "coin": coin,
        "strategy": f"{coin}_币本位空对冲",
        "initial_usdt": INITIAL_USDT,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "final_spot_coin": round(final_spot, 2),
        "final_cash": round(final_cash, 2),
        "final_short_coin": round(final_short, 2),
        "bars": n,
        "equity_series": equity_series,
        "price_series": pd.Series(price_list),
    }


# ============================================================
# 4. 主程序
# ============================================================

def main():
    results = []

    for coin in COINS:
        print(f"正在回测 {coin} ...")
        try:
            df = load_coin_data(coin)
        except Exception as e:
            print(f"{coin} 数据加载失败：{e}")
            continue

        r = backtest_coin_short(df, coin)
        results.append(r)

        print(
            f"{r['strategy']:20s} | "
            f"权益={r['final_equity']:>12.2f} | "
            f"收益={r['total_return_pct']:>7.2f}% | "
            f"回撤={r['max_drawdown_pct']:>7.2f}% | "
            f"现货={r['final_spot_coin']:>12.4f} {coin} | "
            f"现金={r['final_cash']:>10.2f} USDT"
        )

    summary = pd.DataFrame([
        {
            "币种": r["coin"],
            "策略": r["strategy"],
            "初始资金": r["initial_usdt"],
            "最终权益": r["final_equity"],
            "收益率%": r["total_return_pct"],
            "最大回撤%": r["max_drawdown_pct"],
            "最终现货": r["final_spot_coin"],
            "最终现金": r["final_cash"],
            "最终空单": r["final_short_coin"],
            "K线根数": r["bars"],
        }
        for r in results
    ])

    print("\n多币种回测汇总：")
    print(summary.to_string(index=False))

    out_dir = "output"
    os.makedirs(out_dir, exist_ok=True)
    summary.to_csv(os.path.join(out_dir, "dintou_5_summary.csv"), index=False, encoding="utf-8-sig")
    print(f"\n汇总已保存：{os.path.join(out_dir, 'dintou_5_summary.csv')}")


if __name__ == "__main__":
    main()