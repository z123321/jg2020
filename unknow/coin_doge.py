import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta>0, 0)
    loss = -delta.where(delta<0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100/(1+rs))
    return rsi

# 改成狗狗币 DOGE-USD
df = yf.download("DOGE-USD", start="2010-01-01", end="2025-12-31")
df = df[["Open","Close"]].copy()
df = df.reset_index(drop=True)
df["rsi"] = calc_rsi(df["Close"],14)

capital = 10000.0
fee_rate = 0.0008
capital_list = []
trade_results = []

for i in range(14, len(df)):
    rsi_val = df.loc[i, "rsi"]
    curr_o = df.loc[i, "Open"].item()
    curr_c = df.loc[i, "Close"].item()
    signal = 0
    if rsi_val < 30:
        signal = 1   # 超卖做多
    elif rsi_val >70:
        signal = -1  # 超卖做空
    if signal !=0:
        entry = curr_o
        exitp = curr_c
        if signal ==1:
            ret = (exitp / entry)-1
        else:
            ret = (entry / exitp)-1
        net_ret = ret - fee_rate
        capital = capital * (1+net_ret)
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

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.figure(figsize=(12,6))
plt.plot(capital_list, label="DOGE-USD RSI14 30/70，0.08%手续费")
plt.axhline(y=10000, color='r', linestyle='--', label="初始本金")
plt.title("DOGE狗狗币 RSI超买超卖震荡策略回测（带手续费）")
plt.xlabel("交易次数")
plt.ylabel("账户资金")
plt.legend()
plt.grid(alpha=0.3)
plt.show()
