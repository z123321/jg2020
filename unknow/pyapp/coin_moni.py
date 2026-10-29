import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

# ========= 参数 =========
capital0 = 10000.0
fee_rate = 0.0008
max_risk = 0.25
min_risk = 0.05
window_size = 10
init_risk = 0.20
stop_loss_pct = 0.07
trail_stop_pct = 0.10
short_trail_pct = 0.08
bear_long_ratio = 0.35
bear_short_ratio = 1.2

log_file_name = "strategy_ma60_trail.txt"
try:
    with open(log_file_name, "w", encoding="utf-8") as f:
        f.write("K序号,收盘价,账户资金,持仓状态,开仓K(时间),开仓价,平仓价,仓位金额,单笔盈亏,事件\n")
except PermissionError:
    print("⚠️ 无法新建日志文件，文件被占用！")

def write_log(k_idx, price, cap, pos_state, open_k, open_price, close_price, position_amt, pnl, event):
    line = (f"{k_idx},{price:.4f},{cap:.2f},{pos_state},{open_k},{open_price:.4f},"
            f"{close_price:.4f},{position_amt:.2f},{pnl:.2f},{event}")
    print(line)
    try:
        with open(log_file_name, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except PermissionError:
        print("⚠️ 日志文件被占用，本条仅控制台打印，无法写入文件")

# ========= 加载行情 =========
df_raw = yf.download("DOGE-USD", start="2010-01-01", end="2025-12-31", multi_level_index=False)
df_raw = df_raw.reset_index()
df_raw = df_raw[["Open","Close"]].copy().reset_index(drop=True)

def rolling_backtest():
    cap = capital0
    cap_list = []
    long_trades = []
    short_trades = []

    has_long = False
    long_open_k = 0
    long_entry = 0
    long_amt = 0
    long_trail = 0

    has_short = False
    short_open_k = 0
    short_entry = 0
    short_amt = 0
    short_stop_high = 0
    short_lowest_price = None

    for i in range(60, len(df_raw)):
        slice_df = df_raw.iloc[:i+1].copy()
        close_series = slice_df["Close"]
        open_series = slice_df["Open"]
        c = close_series.iloc[-1]
        o = open_series.iloc[-1]

        # 均线计算
        ma10 = close_series.rolling(10).mean().iloc[-1]
        ma60 = close_series.rolling(60).mean().iloc[-1]
        prev_ma10 = close_series.rolling(10).mean().iloc[-2]
        prev2_c = close_series.iloc[-3]
        prev2_ma10 = close_series.rolling(10).mean().iloc[-3]

        # 原有做多信号：ma20上穿ma60金叉
        ma20 = close_series.rolling(20).mean().iloc[-1]
        prev_ma20 = close_series.rolling(20).mean().iloc[-2]
        prev_ma60 = close_series.rolling(60).mean().iloc[-2]
        gold_cross = (prev_ma20 <= prev_ma60) and (ma20 > ma60)

        # ===== 连续三根阴线判断 =====
        o0 = open_series.iloc[-1]
        c0 = close_series.iloc[-1]
        o1 = open_series.iloc[-2]
        c1 = close_series.iloc[-2]
        o2 = open_series.iloc[-3]
        c2 = close_series.iloc[-3]
        bar0_yin = c0 < o0
        bar1_yin = c1 < o1
        bar2_yin = c2 < o2
        three_yin = bar0_yin and bar1_yin and bar2_yin

        # ========= 做空信号【增加MA60过滤：只有价格在MA60下方，才允许开空】 =========
        cond1 = (c < ma10) and (prev2_c >= prev2_ma10)
        cond2 = three_yin
        short_signal = (cond1 or cond2) and (c < ma60)

        # 波动率过滤
        std20 = close_series.rolling(20).std().iloc[-1]
        vol_ok = std20 > np.nanpercentile(close_series.rolling(60).std().dropna(),40)

        event_msg = "无信号"
        pos_state = "空仓"
        open_k_rec = ""
        open_price_rec = 0
        close_price_rec = 0
        pos_amt_rec = 0
        pnl_rec = 0

        if has_long and has_short:
            pos_state = "多空同时持仓"
        elif has_long:
            pos_state = "持有多单"
            open_k_rec = long_open_k
            open_price_rec = long_entry
            pos_amt_rec = long_amt
        elif has_short:
            pos_state = "持有空单"
            open_k_rec = short_open_k
            open_price_rec = short_entry
            pos_amt_rec = short_amt

        # ===== 开多：金叉 + 波动率ok，无多单 =====
        if gold_cross and vol_ok and not has_long:
            has_long = True
            long_open_k = i
            long_entry = c
            long_trail = c * (1 - trail_stop_pct)
            trd = np.array(long_trades[-window_size:])
            if len(trd)>=8:
                win = trd[trd>0]
                lose = trd[trd<=0]
                p_win = len(win)/len(trd)
                avg_win = np.mean(win) if len(win)>0 else 0
                avg_lose = abs(np.mean(lose)) if len(lose)>0 else 0
                if avg_lose>1e-6:
                    kelly = (avg_win * p_win - avg_lose*(1-p_win)) / avg_win
                    risk = np.clip(kelly, min_risk, max_risk)
                else:
                    risk = init_risk
            else:
                risk = init_risk
            risk = risk * bear_long_ratio
            long_amt = cap * risk
            event_msg = "【开多单】均线金叉"
            write_log(i, c, cap, pos_state, i, long_entry, 0, long_amt, 0, event_msg)

        # ===== 开空 =====
        if short_signal and vol_ok and not has_short:
            has_short = True
            short_open_k = i
            short_entry = c
            short_lowest_price = c
            slice_open = df_raw.loc[max(0,i-5):i, "Close"]
            short_stop_high = slice_open.max() * 1.01
            short_amt = cap * risk * bear_short_ratio
            if cond2:
                event_msg = "【开空单】连续3根阴线，MA60趋势过滤，跟踪止盈"
            else:
                event_msg = "【开空单】跌破MA10，MA60趋势过滤，跟踪止盈"
            write_log(i, c, cap, pos_state, i, short_entry, 0, short_amt, 0, event_msg)

        # 多单平仓判断：止损 / 跟踪止盈
        long_sl_hit = has_long and ((c/long_entry -1) <= -stop_loss_pct or c <= long_trail)
        if long_sl_hit:
            pnl = long_amt * ((c / long_entry) - 1) - long_amt*fee_rate
            cap += pnl
            long_trades.append(pnl / long_amt)
            event_msg = "【平多单】止损/跟踪止盈触发"
            write_log(i, c, cap, "平仓", long_open_k, long_entry, c, long_amt, pnl, event_msg)
            has_long = False
            long_amt = 0

        # ========== 空单：更新最低价 + 跟踪止盈判断 ==========
        short_tp_hit = False
        short_sl_hit = False
        if has_short:
            if c < short_lowest_price:
                short_lowest_price = c
            trail_line = short_lowest_price * (1 + short_trail_pct)
            if c >= trail_line:
                short_tp_hit = True
            slice_short = df_raw.loc[i-4:i, "Close"]
            short_stop_high = slice_short.max() * 1.01
            if c >= short_stop_high:
                short_sl_hit = True

        if has_short and (short_sl_hit or short_tp_hit):
            pnl = short_amt * ((short_entry / c) -1) - short_amt*fee_rate
            cap += pnl
            short_trades.append(pnl / short_amt)
            if short_tp_hit:
                event_msg = f"【平空单】跟踪止盈触发，最低价:{short_lowest_price:.2f}"
            else:
                event_msg = f"【平空单】止损触发，止损位:{short_stop_high:.2f}"
            write_log(i, c, cap, "平仓", short_open_k, short_entry, c, short_amt, pnl, event_msg)
            has_short = False
            short_amt =0
            short_stop_high =0
            short_lowest_price = None

        write_log(i, c, cap, pos_state, open_k_rec, open_price_rec, 0, pos_amt_rec, pnl_rec, event_msg)
        cap_list.append(cap)
    return cap_list

def run_long_only():
    cap = capital0
    cap_list = []
    trades = []
    pos =0
    entry=0
    trail=0
    for i in range(60, len(df_raw)):
        slice_df = df_raw.iloc[:i+1]
        close_series = slice_df["Close"]
        open_series = slice_df["Open"]
        c = close_series.iloc[-1]
        ma20 = close_series.rolling(20).mean().iloc[-1]
        ma60 = close_series.rolling(60).mean().iloc[-1]
        prev_ma20 = close_series.rolling(20).mean().iloc[-2]
        prev_ma60 = close_series.rolling(60).mean().iloc[-2]
        gold_cross = (prev_ma20 <= prev_ma60) and (ma20 > ma60)
        std20 = close_series.rolling(20).std().iloc[-1]
        vol_ok = std20 > np.nanpercentile(close_series.rolling(60).std().dropna(),40)
        if gold_cross and vol_ok and pos==0:
            pos = 1
            entry = c
            trail = c*(1-trail_stop_pct)
        sl_hit = pos==1 and ((c/entry -1) <= -stop_loss_pct or c <= trail)
        if sl_hit and pos==1:
            trd = np.array(trades[-window_size:])
            if len(trd)>=8:
                win = trd[trd>0]
                lose = trd[trd<=0]
                p_win = len(win)/len(trd)
                avg_win = np.mean(win) if len(win)>0 else 0
                avg_lose = abs(np.mean(lose)) if len(lose)>0 else 0
                if avg_lose>1e-6:
                    kelly = (avg_win * p_win - avg_lose*(1-p_win)) / avg_win
                    risk = np.clip(kelly, min_risk, max_risk)
                else:
                    risk = init_risk
            else:
                risk = init_risk
            amt = cap * risk
            net = (c/entry -1) - fee_rate
            cap += amt * net
            trades.append(net)
            pos = 0
        cap_list.append(cap)
    return cap_list

cap_both = rolling_backtest()
cap_long = run_long_only()

plt.figure(figsize=(16,8))
plt.plot(cap_long, label="策略1：只做多(逐K)", linewidth=2)
plt.plot(cap_both, label="策略3｜MA60过滤｜空单跟踪止盈", linewidth=2)
plt.axhline(y=capital0, color='r', linestyle='--', label="初始本金10000")
plt.title("DOGE 回测｜MA60趋势过滤 + 空单跟踪止盈")
plt.xlabel("K线序号")
plt.ylabel("账户资金")
plt.legend()
plt.grid(alpha=0.3)
plt.show()

def calc_metrics(cap_list):
    ser = pd.Series(cap_list)
    final = ser.iloc[-1]
    ret = final / capital0 -1
    peak = ser.cummax()
    dd = (ser - peak)/peak
    maxdd = dd.min()
    return final, ret, maxdd

f1,r1,dd1 = calc_metrics(cap_long)
f3,r3,dd3 = calc_metrics(cap_both)
print("\n===== 回测绩效 =====")
print(f"策略1【只做多】｜期末资金：{f1:.2f}，收益率：{r1:.2%}，最大回撤：{dd1:.2%}")
print(f"策略3【MA60+空单跟踪止盈】｜期末资金：{f3:.2f}，收益率：{r3:.2%}，最大回撤：{dd3:.2%}")
print(f"\n✅ 日志输出至：{log_file_name}")
