import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

# ========== 全局参数 ==========
capital0 = 10000.0
max_risk_total = 0.20
min_risk = 0.05
init_risk = 0.20
stop_loss_pct = 0.05
profit_take = 0.06
drop_thresh = -0.08
bear_long_ratio = 0.35
bear_short_ratio = 1.4

# 加载DOGE数据
df = yf.download("DOGE-USD", start="2010-01-01", end="2025-12-31", multi_level_index=False)
df = df[["Open","Close"]].copy()
df = df.reset_index(drop=True)
close_arr = df["Close"].values
df["ma20"] = df["Close"].rolling(window=20).mean()
df["ma60"] = df["Close"].rolling(window=60).mean()
df["std20"] = df["Close"].rolling(window=20).std()
df["ret3"] = df["Close"].pct_change(3)

ma20_arr = df["ma20"].values
ma60_arr = df["ma60"].values
std20_arr = df["std20"].values
ret3_arr = df["ret3"].values

# ===================== 策略1：只做多 =====================
def run_long_only(start_cap):
    cash = start_cap
    cap_list = []
    has_pos = False
    entry_price = 0
    invest_money = 0
    for i in range(60, len(df)):
        start = max(0,i-60)
        ma20_now = ma20_arr[i]
        ma60_now = ma60_arr[i]
        ma20_prev = ma20_arr[i-1]
        ma60_prev = ma60_arr[i-1]
        c = close_arr[i]
        # 改为20分位，降低波动率门槛
        vol_ok = std20_arr[i] > np.nanpercentile(std20_arr[start:i],20)
        gold = (ma20_prev <= ma60_prev) and (ma20_now > ma60_now)
        death = (ma20_prev >= ma60_prev) and (ma20_now < ma60_now)

        pos_value = 0
        if has_pos:
            pos_value = invest_money * (c / entry_price)
        total = cash + pos_value
        cap_list.append(total)

        if not has_pos and gold and vol_ok:
            has_pos = True
            entry_price = c
            invest_money = cash * init_risk
            print(f"【做多开仓】第{i}根K线，价格：{c:.4f}")

        sl_hit = has_pos and ((c/entry_price -1) <= -stop_loss_pct)
        if has_pos and (death or sl_hit):
            cash += invest_money * (c / entry_price)
            print(f"【做多平仓】第{i}根K线，平仓价：{c:.4f}，当前资金：{cash:.2f}")
            has_pos = False
            invest_money = 0
    return cap_list

# ===================== 策略3：锁仓多空 =====================
def run_both_hold():
    cash = capital0
    cap_list = []
    has_long = False
    long_entry = 0
    long_invest = 0
    has_short = False
    short_entry = 0
    short_invest = 0

    for i in range(60, len(df)):
        start = max(0,i-60)
        ma20_now = ma20_arr[i]
        ma60_now = ma60_arr[i]
        ma60_prev = ma60_arr[i-1]
        c = close_arr[i]
        r3 = ret3_arr[i]
        vol_ok = std20_arr[i] > np.nanpercentile(std20_arr[start:i],20)
        gold = (ma20_arr[i-1] <= ma60_prev) and (ma20_now > ma60_now)
        death = (ma20_arr[i-1] >= ma60_prev) and (ma20_now < ma60_now)
        is_bull = ma60_now > ma60_prev
        is_bear = ma60_now < ma60_prev

        pos_value = 0
        if has_long:
            pos_value += long_invest * (c / long_entry)
        if has_short:
            pos_value += short_invest * (short_entry / c)

        total_asset = cash + pos_value
        cap_list.append(total_asset)

        # 开多
        if not has_long and gold and vol_ok:
            has_long = True
            long_entry = c
            risk = init_risk * bear_long_ratio if is_bear else init_risk
            long_invest = cash * risk
            total_margin = long_invest + short_invest
            if total_margin > capital0 * max_risk_total:
                scale = (capital0 * max_risk_total) / total_margin
                long_invest *= scale
                short_invest *= scale
            print(f"【锁仓-多单开仓】第{i}根K线，价格：{c:.4f}")

        # 开空
        open_short_condition = False
        short_risk_coeff = 1.0
        if not has_short and vol_ok:
            if is_bull:
                if r3 < drop_thresh:
                    open_short_condition = True
            else:
                if death or r3 < drop_thresh:
                    open_short_condition = True
                    short_risk_coeff = bear_short_ratio
        if open_short_condition:
            has_short = True
            short_entry = c
            risk = np.clip(init_risk * short_risk_coeff, min_risk, max_risk_total)
            short_invest = cash * risk
            total_margin = long_invest + short_invest
            if total_margin > capital0 * max_risk_total:
                scale = (capital0 * max_risk_total) / total_margin
                long_invest *= scale
                short_invest *= scale
            print(f"【锁仓-空单开仓】第{i}根K线，价格：{c:.4f}")

        # 平多
        long_sl_hit = has_long and ((c/long_entry -1) <= -stop_loss_pct)
        if has_long and long_sl_hit:
            cash += long_invest * (c / long_entry)
            print(f"【锁仓-平多】第{i}根K线，平仓价：{c:.4f}，资金：{cash:.2f}")
            has_long = False
            long_invest = 0

        # 平空
        short_sl_hit = has_short and ((short_entry/c -1) <= -stop_loss_pct)
        short_tp_hit = has_short and ((short_entry/c)-1 >= profit_take)
        if has_short and (gold or short_sl_hit or short_tp_hit):
            cash += short_invest * (short_entry / c)
            print(f"【锁仓-平空】第{i}根K线，平仓价：{c:.4f}，资金：{cash:.2f}")
            has_short = False
            short_invest = 0
    return cap_list

# ===================== 策略4：只做空 =====================
def run_short_only(start_cap):
    cash = start_cap
    cap_list = []
    has_pos = False
    entry_price = 0
    invest_money = 0
    for i in range(60, len(df)):
        start = max(0,i-60)
        ma20_now = ma20_arr[i]
        ma60_now = ma60_arr[i]
        ma20_prev = ma20_arr[i-1]
        ma60_prev = ma60_arr[i-1]
        c = close_arr[i]
        r3 = ret3_arr[i]
        vol_ok = std20_arr[i] > np.nanpercentile(std20_arr[start:i],20)
        gold = (ma20_prev <= ma60_prev) and (ma20_now > ma60_now)
        death = (ma20_prev >= ma60_prev) and (ma20_now < ma60_now)

        pos_value = 0
        if has_pos:
            pos_value = invest_money * (entry_price / c)
        total = cash + pos_value
        cap_list.append(total)

        if not has_pos and vol_ok and (death or r3 < drop_thresh):
            has_pos = True
            entry_price = c
            invest_money = cash * init_risk
            print(f"【做空开仓】第{i}根K线，价格：{c:.4f}")

        sl_hit = has_pos and ((entry_price/c -1) <= -stop_loss_pct)
        tp_hit = has_pos and ((entry_price/c)-1 >= profit_take)
        if has_pos and (gold or sl_hit or tp_hit):
            cash += invest_money * (entry_price / c)
            print(f"【做空平仓】第{i}根K线，平仓价：{c:.4f}，资金：{cash:.2f}")
            has_pos = False
            invest_money = 0
    return cap_list

# ============ 执行 ============
cap_long = run_long_only(capital0)
cap_both = run_both_hold()
cap_short = run_short_only(capital0)

cap_long70 = run_long_only(0.7 * capital0)
cap_short30 = run_short_only(0.3 * capital0)
cap_p5 = [a+b for a,b in zip(cap_long70, cap_short30)]

cap_long30 = run_long_only(0.3 * capital0)
cap_short70 = run_short_only(0.7 * capital0)
cap_p6 = [a+b for a,b in zip(cap_long30, cap_short70)]

# ============ 绘图 ============
plt.figure(figsize=(16,8))
x_axis = np.arange(len(cap_long))
plt.plot(x_axis, cap_long, label="策略1：只做多", linewidth=2)
plt.plot(x_axis, cap_both, label="策略3：锁仓【0.35多单+熊市放大空仓】", linewidth=2)
plt.plot(x_axis, cap_short, label="策略4：增强只做空(抓急跌)", linewidth=2)
plt.plot(x_axis, cap_p5, label="策略5：70%做多+30%做空组合", linewidth=2)
plt.plot(x_axis, cap_p6, label="策略6：30%做多+70%做空组合", linewidth=2)
plt.axhline(y=10000, color='r', linestyle='--', label="初始本金10000")
plt.title("DOGE 5套策略｜锁仓 下跌多单0.35 + 熊市放大空单仓位【固定资金开仓】")
plt.xlabel("K线序号")
plt.ylabel("账户资金")
plt.legend()
plt.grid(alpha=0.3)
plt.show()

# ============ 指标 ============
def calc_metrics(cap_list):
    ser = pd.Series(cap_list)
    final = ser.iloc[-1]
    ret = final / capital0 - 1
    peak = ser.cummax()
    dd = (ser - peak)/peak
    maxdd = dd.min()
    return final, ret, maxdd

f1,r1,dd1 = calc_metrics(cap_long)
f3,r3,dd3 = calc_metrics(cap_both)
f4,r4,dd4 = calc_metrics(cap_short)
f5,r5,dd5 = calc_metrics(cap_p5)
f6,r6,dd6 = calc_metrics(cap_p6)

print("\n===== 策略绩效 =====")
print(f"策略1【只做多】｜期末：{f1:.2f} 收益率：{r1:.2%} 最大回撤：{dd1:.2%}")
print(f"策略3【锁仓多空】｜期末：{f3:.2f} 收益率：{r3:.2%} 最大回撤：{dd3:.2%}")
print(f"策略4【只做空】｜期末：{f4:.2f} 收益率：{r4:.2%} 最大回撤：{dd4:.2%}")
print(f"策略5【70多+30空】｜期末：{f5:.2f} 收益率：{r5:.2%} 最大回撤：{dd5:.2%}")
print(f"策略6【30多+70空】｜期末：{f6:.2f} 收益率：{r6:.2%} 最大回撤：{dd6:.2%}")
