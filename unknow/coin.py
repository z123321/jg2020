import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# 下载行情
df = yf.download("SPY", start="2010-01-01", end="2025-12-31")
df = df[["Open","High","Low","Close"]].copy()
df = df.reset_index(drop=True) # 转为0,1,2数字索引

capital = 10000.0
fee = 0.004
capital_list = [capital]
trade_results = []

# 函数：判断最近N根K线 是否高点抬高、低点抬高
def check_rising_wave(series_high, series_low):
    h = series_high.values
    l = series_low.values
    higher_high = h[-1] > h[-2] > h[-3]
    higher_low = l[-1] > l[-2] > l[-3]
    return higher_high and higher_low

def check_falling_wave(series_high, series_low):
    h = series_high.values
    l = series_low.values
    lower_high = h[-1] < h[-2] < h[-3]
    lower_low = l[-1] < l[-2] < l[-3]
    return lower_high and lower_low

window = 4

for i in range(window, len(df)):
    # iloc：按行号切片，修复Datetime索引报错
    slice_high = df.iloc[i-window:i-1]["High"]
    slice_low = df.iloc[i-window:i-1]["Low"]
    curr_o = df.loc[i, "Open"].item()
    curr_c = df.loc[i, "Close"].item()

    signal = 0
    # 简化：连续抬高高低点=上涨推动浪，准备做多
    if check_rising_wave(slice_high, slice_low):
        signal = 1
    # 连续降低高低点=下跌推动浪，准备做空
    elif check_falling_wave(slice_high, slice_low):
        signal = -1
    else:
        signal = 0

    # 交易执行：切换仓位，当日收盘平仓（沿用日内模式，和前面策略统一）
    if signal != 0:
        entry = curr_o
        exitp = curr_c
        if signal == 1:
            ret = (exitp / entry) -1
        else:
            ret = (entry / exitp) -1
        net_ret = ret - fee
        capital = capital * (1 + net_ret)
        trade_results.append(net_ret)
        capital_list.append(capital)
    else:
        capital_list.append(capital)


# ==========统计指标==========
trade_arr = np.array(trade_results)
win_trades = trade_arr[trade_arr>0]
lose_trades = trade_arr[trade_arr<=0]

win_rate = len(win_trades)/len(trade_arr) if len(trade_arr)>0 else 0
avg_win = np.mean(win_trades) if len(win_trades)>0 else 0
avg_loss = np.mean(lose_trades) if len(lose_trades)>0 else 0
profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else np.inf
total_return = (capital / 10000) - 1

# 最大回撤
cap_series = pd.Series(capital_list)
running_max = cap_series.cummax()
drawdown = (cap_series - running_max) / running_max
max_dd = drawdown.min()

print("="*50)
print(f"初始资金: 10000")
print(f"期末资金: {capital:.2f}")
print(f"总收益率: {total_return:.2%}")
print(f"交易总次数: {len(trade_results)}")
print(f"策略胜率: {win_rate:.2%}")
print(f"平均盈利: {avg_win:.2%}")
print(f"平均亏损: {avg_loss:.2%}")
print(f"盈亏比: {profit_factor:.2f}")
print(f"最大回撤: {max_dd:.2%}")
print("="*50)

# 绘图
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.figure(figsize=(12,6))
plt.plot(capital_list, label="简化波浪量化策略｜SPY 2010-2025，零手续费")
plt.axhline(y=10000, color='r', linestyle='--', label="初始本金")
plt.title("简化艾略特波浪（量化硬规则版本）回测")
plt.xlabel("交易次数")
plt.ylabel("账户资金")
plt.legend()
plt.grid(alpha=0.3)
plt.show()
