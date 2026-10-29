import ccxt
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
# ========== 解决matplotlib中文乱码 ==========
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

# ---------------------- 1. 拉取OKX DOGE/USDT日线 ----------------------
okx = ccxt.okx({
    'enableRateLimit': True,
})

symbol = 'DOGE/USDT'
timeframe = '1d'

print(f"开始从OKX拉取 {symbol} 日线历史K线...")
all_ohlcv = []
# DOGE在OKX上市晚，起始时间改成2020年
since = okx.parse8601('2020-01-01T00:00:00Z')
limit = 300

while True:
    ohlcv = okx.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
    if len(ohlcv) == 0:
        break
    all_ohlcv += ohlcv
    since = ohlcv[-1][0] + 1
    print(f"已获取 {len(all_ohlcv)} 根K线")

df = pd.DataFrame(all_ohlcv, columns=['ts','open','high','low','close','volume'])
df['date'] = pd.to_datetime(df['ts'], unit='ms')
df = df.drop_duplicates(subset=['ts'])
df = df.sort_values('ts').reset_index(drop=True)

close_arr = df['close'].values
date_arr = df['date'].values
print(f"✅ DOGE数据拉取完成，总K线数量：{len(df)}")

# ---------------------- 2. 回测函数（策略完全复用，参数不变） ----------------------
def run_strategy(close, date_list, fall_long_ratio, leverage=1.5, short_stop_loss_pct=0.04, log_filename="okx_doge_log.csv"):
    init_capital = 10000
    capital = init_capital
    equity_curve = []
    long_pos = 0
    short_pos = 0
    entry_short_price = None
    fee_rate = 0.002

    with open(log_filename, "w", encoding="utf-8-sig") as f:
        header = "日期,操作类型,成交价格,多单数量,空单数量,现金余额,账户总权益,多单占账户比例,空单占账户比例,总杠杆,备注\n"
        f.write(header)

    for i in range(len(close)):
        c = close[i]
        dt = str(pd.to_datetime(date_list[i]).date())
        window = 60
        if i < window:
            ma60 = np.mean(close[:i+1])
        else:
            ma60 = np.mean(close[i-window:i])

        is_down_trend = c < ma60
        is_up_trend = c > ma60

        signal_short = False
        if i >=3:
            ret3 = (close[i]/close[i-3]) -1
            if ret3 < -0.08:
                signal_short = True

        action = ""
        note = ""
        # 空单4%止损
        if short_pos>0 and entry_short_price is not None:
            if c >= entry_short_price * (1 + short_stop_loss_pct):
                capital += short_pos * c
                action = "平空单"
                note = "触发4%止损平仓"
                short_pos =0
                entry_short_price = None

        base_cap = init_capital
        # 目标多单
        if is_up_trend:
            target_long = base_cap * 0.5 / c
        else:
            target_long = base_cap * fall_long_ratio / c

        target_short = 0
        if signal_short and short_pos ==0:
            target_short = base_cap *0.7 / c
            entry_short_price = c
            action = "开空单"
            note = "3日跌幅>8% 开空"
        elif short_pos>0:
            target_short = short_pos

        # 杠杆上限控制：多空名义市值总和 ≤ 权益*1.5
        long_val = target_long * c
        short_val = target_short * c
        equity_temp = capital + long_pos*c - short_pos*c
        total_nominal = abs(long_val)+abs(short_val)
        max_nominal = equity_temp * leverage
        if total_nominal > max_nominal and max_nominal>0:
            scale = max_nominal / total_nominal
            target_long *= scale
            target_short *= scale

        # 调仓手续费
        delta_long = target_long - long_pos
        delta_short = target_short - short_pos
        trade_value = abs(delta_long)*c + abs(delta_short)*c
        capital -= trade_value * fee_rate
        long_pos = target_long
        short_pos = target_short

        equity = capital + long_pos * c - short_pos * c
        equity_curve.append(equity)

        long_value = long_pos * c
        short_value = short_pos * c
        pct_long = (long_value / equity)*100 if equity>0 else 0
        pct_short = (short_value / equity)*100 if equity>0 else 0
        total_nom = abs(long_value)+abs(short_value)
        current_leverage = (total_nom / equity) if equity>0 else 0

        if action != "" or abs(delta_long)>1e-8 or abs(delta_short)>1e-8:
            with open(log_filename, "a", encoding="utf-8-sig") as f:
                row = f"{dt},{action},{c:.4f},{long_pos:.4f},{short_pos:.4f},{capital:.2f},{equity:.2f},{pct_long:.2f}%,{pct_short:.2f}%,{current_leverage:.2f}x,{note}\n"
                f.write(row)
    return np.array(equity_curve)

# 两组参数：下跌多单比例0.3 和 0.5
eq_03 = run_strategy(close_arr, date_arr, fall_long_ratio=0.3, leverage=1.5, short_stop_loss_pct=0.04, log_filename="okx_doge_log_03.csv")
eq_05 = run_strategy(close_arr, date_arr, fall_long_ratio=0.5, leverage=1.5, short_stop_loss_pct=0.04, log_filename="okx_doge_log_05.csv")

# 绘图
plt.figure(figsize=(12,6))
plt.plot(eq_03, label="下跌多单比例=0.3 | 1.5x杠杆 |4%止损", linewidth=1.6)
plt.plot(eq_05, label="下跌多单比例=0.5 |1.5x杠杆 |4%止损", linewidth=1.6)
plt.title(f"OKX {symbol} 日线回测｜锁仓策略，1.5倍杠杆，空单4%止损")
plt.xlabel("交易日")
plt.ylabel("总资产")
plt.legend()
plt.grid(alpha=0.3)
plt.show()

# 回测指标
def calc_metrics(equity_arr):
    final = equity_arr[-1]
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr - peak)/peak
    max_dd = np.min(drawdown)
    return final, max_dd

f03, dd03 = calc_metrics(eq_03)
f05, dd05 = calc_metrics(eq_05)

print("==== OKX DOGE 回测结果 ====")
print(f"【下跌多单0.3】最终资金：{f03:.2f}，最大回撤 {dd03:.2%}，日志：okx_doge_log_03.csv")
print(f"【下跌多单0.5】最终资金：{f05:.2f}，最大回撤 {dd05:.2%}，日志：okx_doge_log_05.csv")
