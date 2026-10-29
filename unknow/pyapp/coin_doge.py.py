import ccxt
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# ============================================================
# 1. 数据获取
# ============================================================
def fetch_data(symbol="BTC/USDT", timeframe="1d", days=1825):
    print("\n[1/3] 尝试从 OKX 拉取 BTC/USDT 数据...")
    try:
        exchange = ccxt.okx({
            "enableRateLimit": True,
            "timeout": 10000,
            "proxies": {
                "http": "http://127.0.0.1:19429",
                "https": "http://127.0.0.1:19429",
            },
        })
        exchange.load_markets()
        start_since = exchange.parse8601(
            (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
        )
        all_ohlcv = []
        current_since = start_since
        while True:
            chunk = exchange.fetch_ohlcv(symbol, timeframe, since=current_since, limit=1000)
            if not chunk: break
            all_ohlcv.extend(chunk)
            current_since = chunk[-1][0] + 1
            if current_since >= exchange.milliseconds(): break
        
        df = pd.DataFrame(all_ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        print("  OKX 数据拉取完成")
        return df
    except Exception as e:
        print(f"  OKX 失败: {e}")

    print("\n[2/3] 尝试从 yfinance 拉取 BTC-USD 数据...")
    try:
        start_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        df = yf.download("BTC-USD", start=start_str, progress=False)
        if df.empty: raise ValueError("yfinance 返回空数据")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        if "adj close" in df.columns: df.drop(columns=["adj close"], inplace=True)
        if "date" in df.columns: df.set_index("date", inplace=True)
        df.index.name = "timestamp"
        print("  yfinance 数据拉取完成")
        return df
    except Exception as e:
        print(f"  yfinance 失败: {e}")

    print("\n[3/3] 均不可用，生成模拟数据...")
    np.random.seed(42)
    dates = pd.date_range(end=datetime.now(), periods=days, freq="D")
    returns = np.random.normal(0.001, 0.03, days)
    price = 30000 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({
        "open": price * (1 - np.random.uniform(0, 0.02, days)),
        "high": price * (1 + np.random.uniform(0, 0.02, days)),
        "low": price * (1 - np.random.uniform(0, 0.02, days)),
        "close": price,
        "volume": np.random.randint(1_000_000, 5_000_000, days),
    }, index=dates)
    df.index.name = "timestamp"
    return df


# ============================================================
# 2. 指标计算
# ============================================================
def compute_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ============================================================
# 3. 策略核心逻辑 V11 (BTC 稳健进阶版)
# ============================================================
def run_strategy(df, params):
    capital = 10000.0
    equity_curve = [capital]
    position = None
    trades = []
    cooldown_until_index = -1

    # 预处理指标
    df = df.copy()
    df["ma60"] = df["close"].rolling(60).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_std"] = df["volume"].rolling(20).std()
    df["atr"] = compute_atr(df, period=14)

    for i in range(60, len(df)):
        price = df["close"].iloc[i]
        atr = df["atr"].iloc[i]
        
        if np.isnan(atr) or atr <= 0: atr = None

        # ================== 卖出/平仓逻辑 ==================
        if position:
            position["no_high_days"] += 1
            if price > position["best_price"]:
                position["best_price"] = price
                position["no_high_days"] = 0

            # 【核心逻辑】根据当前盈亏动态调整移动止损倍数 (BTC 专属)
            profit_pct = price / position["entry_price"] - 1
            current_trail_mult = params["trail_mult_base"] 
            
            if profit_pct > 0.15: # BTC 盈利>15%，极度锁死利润 (0.5倍 ATR)
                current_trail_mult = params["trail_mult_super_tight"]
            elif profit_pct > 0.08: # BTC 盈利>8%，收紧止损 (1.0倍 ATR)
                current_trail_mult = params["trail_mult_tight"]

            # 移动止损线
            if atr is not None:
                trail_stop = position["best_price"] - current_trail_mult * atr
                if trail_stop > position["stop"]:
                    position["stop"] = trail_stop

            # 触发平仓
            should_exit = False
            exit_reason = ""
            if price <= position["stop"]:
                should_exit = True
                exit_reason = "移动止损/锁利"
            elif position["no_high_days"] >= params["exit_no_high_days"]:
                should_exit = True
                exit_reason = "N日未创新高"

            if should_exit:
                profit_pct = price / position["entry_price"] - 1
                pnl = position["capital"] * profit_pct
                capital = position["capital"] * (1 + profit_pct)
                
                # 亏损触发冷却期
                if pnl < 0:
                    cooldown_until_index = i + params["loss_cooldown"]

                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": df.index[i],
                    "entry_price": position["entry_price"],
                    "exit_price": price,
                    "pnl": pnl,
                    "reason": exit_reason,
                })
                position = None

        # ================== 买入逻辑 ==================
        if i > cooldown_until_index and position is None and atr is not None:
            ma60 = df["ma60"].iloc[i]
            vol_ma20 = df["vol_ma20"].iloc[i]
            vol_std = df["vol_std"].iloc[i]

            # 纯粹量价信号：价格突破60日均线 + 异常放量
            trend_ok = price > ma60
            vol_ok = df["volume"].iloc[i] > (vol_ma20 + 1.5 * vol_std)

            if trend_ok and vol_ok:
                risk = capital * params["risk_pct"]
                stop_dist = params["atr_sl_mult"] * atr
                size_usdt = risk / (stop_dist / price)
                
                # 限制最大仓位
                size_usdt = min(size_usdt, capital * params["max_position"])

                if size_usdt > 0:
                    stop_price = price - stop_dist
                    position = {
                        "entry_price": price,
                        "stop": stop_price,
                        "best_price": price,
                        "no_high_days": 0,
                        "capital": capital,
                        "entry_time": df.index[i],
                    }

        # ================== 记录资金曲线 ==================
        current_equity = capital
        if position:
            profit_pct = price / position["entry_price"] - 1
            current_equity = position["capital"] * (1 + profit_pct)
        equity_curve.append(current_equity)

    return equity_curve, trades


# ============================================================
# 4. 主程序
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("BTC 趋势策略 V11 (稳健进阶版)")
    print("=" * 50)

    df = fetch_data()

    required_cols = {"open", "high", "low", "close", "volume"}
    if df.empty or not required_cols.issubset(df.columns):
        print("\n错误：数据列不完整")
        print("当前列:", list(df.columns))
        exit()

    params = {
        "risk_pct": 0.02,           # 单笔风险 2%
        "max_position": 0.3,        # 最大仓位 30%
        "atr_sl_mult": 2.0,         # 初始硬止损 2倍 ATR (BTC 波动小，2倍足够)
        
        # 【核心】动态止盈阶梯 (BTC 专属)
        "trail_mult_base": 2.0,     # 默认止损倍数
        "trail_mult_tight": 1.0,    # 盈利>8%时的止损倍数
        "trail_mult_super_tight": 0.5, # 盈利>15%时的止损倍数
        
        "exit_no_high_days": 7,     # 连续7天未创新高平仓
        "loss_cooldown": 3          # 亏损后冷却 3 根 K 线
    }

    curve, trades = run_strategy(df, params)

    final = curve[-1]
    ret = final / 10000.0 - 1
    ser = pd.Series(curve)
    maxdd = ((ser - ser.cummax()) / ser.cummax()).min()

    print(f"\n===== 策略绩效 =====")
    print(f"  期末资金: {final:.2f}")
    print(f"  收益率:   {ret:.2%}")
    print(f"  最大回撤: {maxdd:.2%}")
    print(f"  交易次数: {len(trades)}")

    if trades:
        print("\n===== 交易记录 =====")
        for t in trades:
            print(
                f"  {t['entry_time'].date()} -> {t['exit_time'].date()}  "
                f"买={t['entry_price']:.2f} 卖={t['exit_price']:.2f}  "
                f"盈亏={t['pnl']:.2f} ({t['reason']})"
            )

    plt.figure(figsize=(12, 6))
    plt.plot(curve, label="资金曲线", linewidth=1.5)
    plt.axhline(10000, color="r", linestyle="--", label="初始本金")
    plt.title(f"BTC 趋势策略 V11 | 收益率: {ret:.2%} | 最大回撤: {maxdd:.2%}")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.show()