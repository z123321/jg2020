import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============ 通用配置 ============
USE_TESTNET = False          # True=OKX 模拟盘，False=OKX 正式环境/回测
PROXY_HTTP  = "http://127.0.0.1:7890"   # 按你 clash 实际端口改
PROXY_HTTPS = "http://127.0.0.1:7890"
TIMEFRAME   = "1d"
LIMIT       = 1000
INIT_CAPITAL = 100000.0
SLIPPAGE    = 0.0005       # 0.05% 滑点

# ============ 分币种参数 ============
COIN_PARAMS = {
    "BTC/USDT":  {"ma": 50, "atr_mult": 2.8, "use_trailing": True,  "min_trade": 0.001},
    "ETH/USDT":  {"ma": 50, "atr_mult": 2.8, "use_trailing": True,  "min_trade": 0.001},
    "DOGE/USDT": {"ma": 30, "atr_mult": 3.2, "use_trailing": True,  "min_trade": 1.0},
    "SOL/USDT":  {"ma": 30, "atr_mult": 3.2, "use_trailing": True,  "min_trade": 0.01},
}

# ============ 拉取 OKX 数据 ============
def fetch_okx_data(symbol: str):
    exchange = ccxt.okx({
        "enableRateLimit": True,
        "proxies": {"http": PROXY_HTTP, "https": PROXY_HTTPS},
    })
    if USE_TESTNET:
        exchange.set_sandbox_mode(True)
    raw = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT)
    df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["time"], unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.date
    df = df[["date", "open", "high", "low", "close", "volume"]].dropna()
    df.reset_index(drop=True, inplace=True)
    return df

# ============ 回测 ============
def backtest(symbol: str, df: pd.DataFrame, params: dict):
    ma = params["ma"]
    atr_mult = params["atr_mult"]
    use_trailing = params["use_trailing"]
    min_trade = params["min_trade"]

    df["ma"] = df["close"].rolling(ma).mean()
    df["prev_close"] = df["close"].shift(1)
    df["prev_high"] = df["high"].shift(1)
    df["prev_low"] = df["low"].shift(1)
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            (df["high"] - df["prev_close"]).abs(),
            (df["low"] - df["prev_close"]).abs(),
        ),
    )
    df["atr"] = df["tr"].rolling(14).mean()

    capital = INIT_CAPITAL
    position = 0.0          # 持仓数量，>0 多，<0 空
    entry_price = 0.0
    atr_lock = 0.0
    trail_stop = 0.0
    last_trade_day = -999
    COOLDOWN = 1
    RISK_PER_TRADE = 0.10

    trades = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        date = row["date"]
        close = row["close"]
        high = row["high"]
        low = row["low"]

        if pd.isna(prev["ma"]) or pd.isna(row["ma"]) or pd.isna(prev["atr"]):
            continue

        trend_up = close >= row["ma"]
        trend_down = close < row["ma"]

        cdp = (prev["high"] + prev["low"] + prev["close"]) / 3.0
        ah = cdp + (prev["high"] - prev["low"])
        al = cdp - (prev["high"] - prev["low"])
        nh = prev["high"] + 2.0 * (cdp - prev["low"])
        nl = prev["low"] + 2.0 * (cdp - prev["high"])

        current_atr = prev["atr"]
        if current_atr <= 0:
            continue

        # ---------- 多头平仓 ----------
        if position > 0:
            stop_loss_price = entry_price - atr_mult * atr_lock
            if low <= stop_loss_price:
                exit_price = stop_loss_price * (1 - SLIPPAGE)
                capital += position * exit_price
                pnl_pct = (exit_price - entry_price) / entry_price
                trades.append({"date": date, "side": "long", "pnl": pnl_pct, "reason": "ATR止损"})
                position = 0.0
                last_trade_day = i
                continue

            if use_trailing and trend_up:
                new_trail = prev["high"] - atr_mult * current_atr
                trail_stop = max(trail_stop, new_trail) if trail_stop > 0 else new_trail
                if low <= trail_stop:
                    exit_price = trail_stop * (1 - SLIPPAGE)
                    capital += position * exit_price
                    pnl_pct = (exit_price - entry_price) / entry_price
                    trades.append({"date": date, "side": "long", "pnl": pnl_pct, "reason": "ATR跟踪止盈"})
                    position = 0.0
                    last_trade_day = i
                    continue

            if not trend_up:
                if high >= nh:
                    exit_price = nh * (1 - SLIPPAGE)
                    capital += position * exit_price
                    pnl_pct = (exit_price - entry_price) / entry_price
                    trades.append({"date": date, "side": "long", "pnl": pnl_pct, "reason": "NH卖"})
                    position = 0.0
                    last_trade_day = i
                    continue
                if close < cdp:
                    exit_price = close * (1 - SLIPPAGE)
                    capital += position * exit_price
                    pnl_pct = (exit_price - entry_price) / entry_price
                    trades.append({"date": date, "side": "long", "pnl": pnl_pct, "reason": "破CDP离场"})
                    position = 0.0
                    last_trade_day = i
                    continue

        # ---------- 空头平仓 ----------
        elif position < 0:
            qty = -position
            stop_loss_price = entry_price + atr_mult * atr_lock
            if high >= stop_loss_price:
                exit_price = stop_loss_price * (1 + SLIPPAGE)
                pnl = qty * (entry_price - exit_price)
                capital += qty * entry_price + pnl
                pnl_pct = (entry_price - exit_price) / entry_price
                trades.append({"date": date, "side": "short", "pnl": pnl_pct, "reason": "ATR止损(空)"})
                position = 0.0
                last_trade_day = i
                continue

            if use_trailing and trend_down:
                new_trail = prev["low"] + atr_mult * current_atr
                trail_stop = min(trail_stop, new_trail) if trail_stop > 0 else new_trail
                if high >= trail_stop:
                    exit_price = trail_stop * (1 + SLIPPAGE)
                    pnl = qty * (entry_price - exit_price)
                    capital += qty * entry_price + pnl
                    pnl_pct = (entry_price - exit_price) / entry_price
                    trades.append({"date": date, "side": "short", "pnl": pnl_pct, "reason": "ATR跟踪止盈(空)"})
                    position = 0.0
                    last_trade_day = i
                    continue

            if not trend_down:
                if low <= nl:
                    exit_price = nl * (1 + SLIPPAGE)
                    pnl = qty * (entry_price - exit_price)
                    capital += qty * entry_price + pnl
                    pnl_pct = (entry_price - exit_price) / entry_price
                    trades.append({"date": date, "side": "short", "pnl": pnl_pct, "reason": "NL止盈"})
                    position = 0.0
                    last_trade_day = i
                    continue
                if close > cdp:
                    exit_price = close * (1 + SLIPPAGE)
                    pnl = qty * (entry_price - exit_price)
                    capital += qty * entry_price + pnl
                    pnl_pct = (entry_price - exit_price) / entry_price
                    trades.append({"date": date, "side": "short", "pnl": pnl_pct, "reason": "破CDP离场(空)"})
                    position = 0.0
                    last_trade_day = i
                    continue

        # ---------- 开仓 ----------
        if position == 0 and i - last_trade_day > COOLDOWN:
            risk_cap = capital * RISK_PER_TRADE

            if trend_up and high >= nh:
                qty = max(min_trade, round(risk_cap / close / min_trade) * min_trade)
                cost = qty * close * (1 + SLIPPAGE)
                if cost <= capital:
                    capital -= cost
                    position = qty
                    entry_price = close
                    atr_lock = current_atr
                    trail_stop = 0.0
                    last_trade_day = i

            elif trend_down and low <= nl:
                qty = max(min_trade, round(risk_cap / close / min_trade) * min_trade)
                margin = qty * close * (1 + SLIPPAGE)
                if margin <= capital:
                    capital -= margin
                    position = -qty
                    entry_price = close
                    atr_lock = current_atr
                    trail_stop = 0.0
                    last_trade_day = i

    # ---------- 期末平仓 ----------
    if position > 0:
        exit_price = df.iloc[-1]["close"] * (1 - SLIPPAGE)
        capital += position * exit_price
        pnl_pct = (exit_price - entry_price) / entry_price
        trades.append({"date": df.iloc[-1]["date"], "side": "long", "pnl": pnl_pct, "reason": "期末平仓"})
    elif position < 0:
        qty = -position
        exit_price = df.iloc[-1]["close"] * (1 + SLIPPAGE)
        pnl = qty * (entry_price - exit_price)
        capital += qty * entry_price + pnl
        pnl_pct = (entry_price - exit_price) / entry_price
        trades.append({"date": df.iloc[-1]["date"], "side": "short", "pnl": pnl_pct, "reason": "期末平仓"})

    return capital, trades
# ============ 主流程 ============
symbols = list(COIN_PARAMS.keys())
all_results = {}

for sym in symbols:
    print(f"\n{'='*60}")
    print(f"正在从 OKX 拉取 {sym} 数据...")
    df = fetch_okx_data(sym)
    print(f"拉取成功！共 {len(df)} 条数据。")
    print(df.head().to_string(index=False))
    print("...")
    print(df.tail().to_string(index=False))

    params = COIN_PARAMS[sym]
    final_cap, trades = backtest(sym, df, params)
    pnl = final_cap - INIT_CAPITAL
    ret = pnl / INIT_CAPITAL * 100
    win = sum(1 for t in trades if t["pnl"] > 0)
    total = len(trades)
    win_rate = win / total * 100 if total else 0
    avg_pnl = np.mean([t["pnl"] for t in trades]) * 100 if trades else 0

    all_results[sym] = {
        "rows": len(df),
        "trades": total,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "pnl": pnl,
        "ret": ret,
        "trades_detail": trades,
        "params": params,
    }

# ============ 汇总 ============
print("\n" + "=" * 80)
print("汇总统计（分币参数版）")
print("=" * 80)
print(f"{'标的':<14}{'参数':<32}{'数据行':>7}{'交易次数':>9}{'胜率':>9}{'平均单笔':>11}{'净盈亏':>14}{'收益率':>10}")
print("-" * 106)

total_pnl = 0.0
for sym, res in all_results.items():
    p = res["params"]
    param_str = f"MA{p['ma']}/ATR{p['atr_mult']}/Trl{'Y' if p['use_trailing'] else 'N'}"
    print(
        f"{sym:<14}{param_str:<32}{res['rows']:>7}{res['trades']:>9}"
        f"{res['win_rate']:>8.2f}%{res['avg_pnl']:>+10.4f}%"
        f"{res['pnl']:>+13.2f}{res['ret']:>+9.2f}%"
    )
    total_pnl += res["pnl"]

print("-" * 106)
print(f"{'合计':<53}{total_pnl:>+13.2f}{total_pnl/INIT_CAPITAL*100:>+9.2f}%")

# 按卖出原因统计
print("\n--- 按平仓原因统计 ---")
reason_stats = {}
for sym, res in all_results.items():
    for t in res["trades_detail"]:
        r = t["reason"]
        reason_stats.setdefault(r, []).append(t["pnl"])

for r, pnls in reason_stats.items():
    cnt = len(pnls)
    wr = sum(1 for p in pnls if p > 0) / cnt * 100 if cnt else 0
    avg = np.mean(pnls) * 100
    print(f"  {r}：{cnt}笔  胜率{wr:.1f}%  平均{avg:+.4f}%")