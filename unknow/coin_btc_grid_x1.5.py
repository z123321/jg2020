import numpy as np
import matplotlib.pyplot as plt
import yfinance as yf
import os
from datetime import datetime

# ========== Clash代理设置 ==========
proxy_addr = "http://127.0.0.1:7890"
os.environ['HTTP_PROXY'] = proxy_addr
os.environ['HTTPS_PROXY'] = proxy_addr

# ========== 拉DOGE-USD 5年日线行情 ==========
print("正在拉取DOGE-USD真实历史行情...")
doge = yf.Ticker("DOGE-USD")
df = doge.history(period="5y", interval="1d")
print(f"获取到 {len(df)} 条K线")
close_arr = df["Close"].values
date_arr = df.index.values # 保存日期用于日志

# ---------------------- 锁仓策略【固定本金基数 +1.5倍杠杆上限，空单4%止损】+ 交易日志输出 ----------------------
def run_strategy(close, date_list, fall_long_ratio, leverage=1.5, short_stop_loss_pct=0.04, log_filename="trade_log.csv"):
    init_capital = 10000
    capital = init_capital
    equity_curve = []
    long_pos = 0
    short_pos = 0
    entry_short_price = None
    fee_rate = 0.002

    # 初始化交易日志文件，写入表头
    with open(log_filename, "w", encoding="utf-8-sig") as f:
        header = "日期,操作类型,成交价格,多单数量,空单数量,现金余额,账户总权益,多单占账户比例,空单占账户比例,总杠杆,备注\n"
        f.write(header)

    for i in range(len(close)):
        c = close[i]
        dt = np.datetime_as_string(date_list[i], unit='D')
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

        action = ""
        note = ""
        # 空单止损检查：价格上涨超过4%，全部平空
        if short_pos > 0 and entry_short_price is not None:
            if c >= entry_short_price * (1 + short_stop_loss_pct):
                # 全部平空
                capital += short_pos * c
                action = "平空单"
                note = "触发4%止损平仓"
                short_pos = 0
                entry_short_price = None

        # 仓位计算永远用初始10000本金，防止无限复利爆炸
        base_cap = init_capital

        # 目标多单仓位
        if is_up_trend:
            target_long = base_cap * 0.5 / c
        else:
            target_long = base_cap * fall_long_ratio / c

        # 目标空单仓位
        target_short = 0
        if signal_short and short_pos == 0:
            target_short = base_cap * 0.7 / c
            entry_short_price = c
            action = "开空单"
            note = "3日跌幅>8% 开空"
        elif short_pos > 0:
            target_short = short_pos

        # ========== 杠杆限制逻辑：总名义仓位绝对值之和不能超过权益*leverage ==========
        long_val = target_long * c
        short_val = target_short * c
        total_nominal = abs(long_val) + abs(short_val)
        equity_temp = capital + long_pos*c - short_pos*c
        max_nominal = equity_temp * leverage
        if total_nominal > max_nominal and max_nominal>0:
            scale = max_nominal / total_nominal
            target_long *= scale
            target_short *= scale

        # 调仓
        delta_long = target_long - long_pos
        delta_short = target_short - short_pos
        trade_value = abs(delta_long)*c + abs(delta_short)*c
        capital -= trade_value * fee_rate
        long_pos = target_long
        short_pos = target_short

        equity = capital + long_pos * c - short_pos * c
        equity_curve.append(equity)

        # 计算仓位占比、总杠杆
        long_value = long_pos * c
        short_value = short_pos * c
        pct_long = (long_value / equity) * 100 if equity>0 else 0
        pct_short = (short_value / equity) * 100 if equity>0 else 0
        total_nom = abs(long_value)+abs(short_value)
        current_leverage = (total_nom / equity) if equity>0 else 0

        # 只要有调仓/平仓动作，写入日志
        if action != "" or abs(delta_long) > 1e-8 or abs(delta_short) >1e-8:
            with open(log_filename, "a", encoding="utf-8-sig") as f:
                row = f"{dt},{action},{c:.4f},{long_pos:.4f},{short_pos:.4f},{capital:.2f},{equity:.2f},{pct_long:.2f}%,{pct_short:.2f}%,{current_leverage:.2f}x,{note}\n"
                f.write(row)
    return np.array(equity_curve)

# 跑两组参数 0.3 和0.5，1.5倍杠杆上限，4%止损
eq_03 = run_strategy(close_arr, date_arr, fall_long_ratio=0.3, leverage=1.5, short_stop_loss_pct=0.04, log_filename="trade_log_03_lev1.5_stop4pct.csv")
eq_05 = run_strategy(close_arr, date_arr, fall_long_ratio=0.5, leverage=1.5, short_stop_loss_pct=0.04, log_filename="trade_log_05_lev1.5_stop4pct.csv")

# 绘图对比
plt.figure(figsize=(12,6))
plt.plot(eq_03, label="下跌多单比例=0.3 | 1.5x杠杆 | 4%止损", linewidth=1.6)
plt.plot(eq_05, label="下跌多单比例=0.5 | 1.5x杠杆 | 4%止损", linewidth=1.6)
plt.title("真实DOGE历史行情 | 锁仓策略【固定本金+1.5倍杠杆，空单4%止损】0.3 VS 0.5")
plt.xlabel("交易日")
plt.ylabel("总资产")
plt.legend()
plt.grid(alpha=0.3)
plt.show()

# 计算回测指标
def calc_metrics(equity_arr):
    final = equity_arr[-1]
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr - peak) / peak
    max_drawdown_pct = np.min(drawdown)
    return final, max_drawdown_pct

f03, dd03 = calc_metrics(eq_03)
f05, dd05 = calc_metrics(eq_05)

print("=====回测结果【1.5倍杠杆，空单4%止损】=====")
print(f"【下跌多单0.3】最终资金：{f03:.2f}，最大回撤{dd03:.2%}，日志: trade_log_03_lev1.5_stop4pct.csv")
print(f"【下跌多单0.5】最终资金：{f05:.2f}，最大回撤{dd05:.2%}，日志: trade_log_05_lev1.5_stop4pct.csv")
print("\n✅ 日志已更新，止损修改为4%，减少短期反弹被扫止损")
