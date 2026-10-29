import numpy as np
import matplotlib.pyplot as plt

# ---------------------- 生成模拟DOGE行情（不用联网） ----------------------
np.random.seed(42)
n_days = 1800
base = 0.002
price = np.zeros(n_days)
price[0] = base
vol = 0.03
trend = np.linspace(0, 1.2, n_days)
for i in range(1, n_days):
    shock = np.random.normal(0, vol)
    price[i] = price[i-1] * (1 + shock + 0.0008*trend[i] - 0.0005*np.sin(i/80))
close_arr = price

# ---------------------- 动态锁仓策略【修复：仓位基数锁定初始资金】，空单3%止损 ----------------------
def run_strategy(close, fall_long_ratio, short_stop_loss_pct=0.03):
    init_capital = 10000
    capital = init_capital
    equity_curve = []
    long_pos = 0
    short_pos = 0
    entry_short_price = None
    fee_rate = 0.002

    for i in range(len(close)):
        c = close[i]
        # 60周期均线判断趋势
        window = 60
        if i < window:
            ma60 = np.mean(close[:i+1])
        else:
            ma60 = np.mean(close[i-window:i])

        is_down_trend = c < ma60
        is_up_trend = c > ma60

        # 增强做空信号：3日跌幅>8%
        signal_short = False
        if i >= 3:
            ret3 = (close[i] / close[i-3]) - 1
            if ret3 < -0.08:
                signal_short = True

        # 空单止损检查：价格上涨超过3%，全部平空
        if short_pos > 0 and entry_short_price is not None:
            if c >= entry_short_price * (1 + short_stop_loss_pct):
                capital += short_pos * c
                short_pos = 0
                entry_short_price = None

        # ==========核心修复：仓位计算永远用初始10000，而不是实时capital，防止无限复利爆炸==========
        base_cap = init_capital

        # 目标多单仓位
        if is_up_trend:
            target_long = base_cap * 0.5 / c
        else:
            target_long = base_cap * fall_long_ratio / c

        # 目标空单仓位
        if signal_short and short_pos == 0:
            target_short = base_cap * 0.7 / c
            entry_short_price = c
        elif short_pos > 0:
            target_short = short_pos
        else:
            target_short = 0

        # 调仓手续费
        delta_long = target_long - long_pos
        delta_short = target_short - short_pos
        trade_value = abs(delta_long)*c + abs(delta_short)*c
        capital -= trade_value * fee_rate
        long_pos = target_long
        short_pos = target_short

        equity = capital + long_pos * c - short_pos * c
        equity_curve.append(equity)
    return np.array(equity_curve)

# 跑两组参数
eq_03 = run_strategy(close_arr, fall_long_ratio=0.3)
eq_05 = run_strategy(close_arr, fall_long_ratio=0.5)

# 绘图
plt.figure(figsize=(12,6))
plt.plot(eq_03, label="下跌多单比例=0.3", linewidth=1.6)
plt.plot(eq_05, label="下跌多单比例=0.5", linewidth=1.6)
plt.title("锁仓策略【固定本金基数，空单3%止损】：下跌行情多单仓位 0.3 VS 0.5")
plt.xlabel("交易日")
plt.ylabel("总资产")
plt.legend()
plt.grid(alpha=0.3)
plt.show()

# 指标计算
def calc_metrics(equity_arr):
    final = equity_arr[-1]
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr - peak) / peak
    max_drawdown_pct = np.min(drawdown)
    return final, max_drawdown_pct

f03, dd03 = calc_metrics(eq_03)
f05, dd05 = calc_metrics(eq_05)

print(f"【下跌多单0.3】最终资金：{f03:.2f}，最大回撤{dd03:.2%}")
print(f"【下跌多单0.5】最终资金：{f05:.2f}，最大回撤{dd05:.2%}")
