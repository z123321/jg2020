import datetime
import math
import traceback
from collections import defaultdict

import pandas as pd
import baostock as bs
import akshare as ak
import requests


def fetch_baostock(code: str, start_date: str, end_date: str):
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败：{lg.error_msg}")

    fields = "date,code,open,high,low,close,volume,amount"
    rs = bs.query_history_k_data_plus(
        code, fields, start_date=start_date, end_date=end_date,
        frequency="d", adjustflag="2",
    )

    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()

    if not rows:
        raise RuntimeError("BaoStock 返回空数据")

    df = pd.DataFrame(rows, columns=rs.fields)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    return df.sort_values("date").reset_index(drop=True)


def fetch_akshare(code: str, start_date: str, end_date: str):
    m = code.split(".")
    if len(m) != 2:
        raise ValueError(f"代码格式错误：{code}")
    market, raw = m
    symbol = f"1{raw}" if market == "sh" else f"0{raw}" if market == "sz" else None
    if not symbol:
        raise ValueError(f"不支持的市场：{market}")

    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""), adjust="qfq",
    )
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
        "收盘": "close", "成交量": "volume", "成交额": "amount"
    })
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["date", "open", "high", "low", "close", "volume", "amount"]] \
        .dropna(subset=["date", "open", "high", "low", "close"]) \
        .sort_values("date").reset_index(drop=True)


def fetch_sina(code: str, start_date: str, end_date: str):
    m = code.split(".")
    if len(m) != 2:
        raise ValueError(f"代码格式错误：{code}")
    market, raw = m
    symbol = f"sh{raw}" if market == "sh" else f"sz{raw}" if market == "sz" else None
    if not symbol:
        raise ValueError(f"不支持的市场：{market}")

    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    resp = requests.get(url, params={
        "symbol": symbol, "scale": "240", "ma": "no", "datalen": "5000"
    }, timeout=15)
    resp.raise_for_status()

    rows = []
    for item in resp.json():
        try:
            dt = datetime.datetime.strptime(item.get("day", ""), "%Y-%m-%d")
        except Exception:
            continue
        if pd.Timestamp(start_date) <= dt <= pd.Timestamp(end_date):
            rows.append({
                "date": dt,
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item["volume"]),
                "amount": float(item.get("amount", 0))
            })

    if not rows:
        raise RuntimeError("新浪财经返回空数据")
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def fetch_klines(code: str, start_date: str, end_date: str):
    sources = [
        ("BaoStock", fetch_baostock),
        ("AKShare", fetch_akshare),
        ("新浪财经", fetch_sina)
    ]
    for name, fn in sources:
        try:
            df = fn(code, start_date, end_date)
            if df is not None and not df.empty and len(df) >= 30:
                print(f"数据源：{name}，共 {len(df)} 行")
                return df
        except Exception as e:
            print(f"{name} 失败：{e}")
    raise RuntimeError("所有数据源都失败")


def compute_cdp(prev_open, prev_high, prev_low, prev_close):
    cdp = (prev_high + prev_low + 2 * prev_close) / 4.0
    nh = 2 * cdp - prev_low
    nl = 2 * cdp - prev_high
    return nh, cdp, nl


def backtest_cdp_atr_fixed(
    symbol: str, start_date: str = "2024-01-01", end_date: str = None,
    capital: float = 100_000, commission_rate: float = 0.00025, min_commission: float = 5.0,
    stamp_duty_rate: float = 0.0005, atr_period: int = 14, atr_multiplier: float = 2.0,
    position_ratio: float = 0.2,
):
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")
    df = fetch_klines(symbol, start_date, end_date)

    # ================= 计算 ATR =================
    df["prev_close"] = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = abs(df["high"] - df["prev_close"])
    tr3 = abs(df["low"] - df["prev_close"])
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(window=atr_period).mean()

    # ================= 计算 CDP =================
    df["prev_open"] = df["open"].shift(1)
    df["prev_high"] = df["high"].shift(1)
    df["prev_low"] = df["low"].shift(1)

    cdp_rows = []
    for i in range(len(df)):
        row = df.iloc[i]
        if i == 0 or pd.isna(row["prev_close"]):
            cdp_rows.append({"nh": None, "cdp": None, "nl": None})
            continue
        nh, cdp, nl = compute_cdp(
            row["prev_open"], row["prev_high"], row["prev_low"], row["prev_close"]
        )
        cdp_rows.append({"nh": nh, "cdp": cdp, "nl": nl})

    cdp_df = pd.DataFrame(cdp_rows)
    df["nh"] = cdp_df["nh"]
    df["cdp"] = cdp_df["cdp"]
    df["nl"] = cdp_df["nl"]

    equity = capital
    shares = 0
    buy_price = 0.0
    buy_signal = ""
    buy_date = None
    trades = []
    yesterday_buy_shares = 0
    current_atr_stop = 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]  # 【关键】获取前一日数据用于ATR止损
        
        open_price = curr["open"]
        high_price = curr["high"]
        low_price = curr["low"]
        close_price = curr["close"]
        nh = curr["nh"]
        cdp = curr["cdp"]
        prev_h = curr["prev_high"]
        
        # 【修复】使用昨日的ATR作为今日买入/持仓的波动率基准
        prev_atr = prev["atr"] 
        curr_prev_close = curr["prev_close"]

        # 跳过无效数据
        if any(v is None or (isinstance(v, float) and math.isnan(v))
               for v in [nh, cdp, prev_h, prev_atr]):
            yesterday_buy_shares = 0
            continue

        # 复权跳变保护
        if curr_prev_close > 0 and abs(open_price / curr_prev_close - 1) > 0.20:
            yesterday_buy_shares = 0
            continue

        sellable = max(0, shares - yesterday_buy_shares)

        # ================= 卖出逻辑 =================
        if shares > 0 and sellable > 0:
            sell_signal = None
            sell_price = None

            # 1. ATR 自适应止损（优先级最高）
            if low_price <= current_atr_stop:
                sell_signal, sell_price = "ATR止损", current_atr_stop
            
            # 2. NH 止盈
            elif nh > 0 and high_price >= nh:
                sell_signal, sell_price = "NH卖", nh
            
            # 3. 【优化】盘中跌破 CDP 直接止损（替代原来的收盘卖）
            elif low_price < cdp:
                sell_signal, sell_price = "破CDP止损", cdp

            if sell_signal and sell_price is not None:
                revenue = sellable * sell_price
                commission = max(min_commission, revenue * commission_rate)
                stamp = revenue * stamp_duty_rate
                cost = revenue - commission - stamp

                pnl = cost - sellable * buy_price
                pnl_pct = pnl / (sellable * buy_price) if buy_price > 0 else 0

                equity += cost
                shares -= sellable

                trades.append({
                    "buy_date": buy_date, "sell_date": curr["date"], "signal": buy_signal,
                    "sell_reason": sell_signal, "buy_price": buy_price, "sell_price": sell_price,
                    "pnl_pct": pnl_pct, "pnl": pnl, "equity": equity,
                })
                buy_price, buy_signal, buy_date = 0.0, "", None
                current_atr_stop = 0.0

        yesterday_buy_shares = 0

        # ================= 买入逻辑 (仅AH突破) =================
        if shares == 0:
            target_signal = None
            target_price = None

            if prev_h > nh:
                target_signal = "AH突破买"
                target_price = open_price

            if target_signal and target_price is not None:
                use_capital = equity * position_ratio
                raw_shares = int(use_capital / target_price / 100) * 100
                if raw_shares > 0:
                    cost = raw_shares * target_price
                    commission = max(min_commission, cost * commission_rate)
                    total_cost = cost + commission

                    if total_cost <= equity:
                        equity -= total_cost
                        shares = raw_shares
                        buy_price = target_price
                        buy_signal = target_signal
                        buy_date = curr["date"]
                        yesterday_buy_shares = raw_shares
                        
                        # 【修复】使用昨日ATR计算止损位，避免当日极端波动干扰
                        current_atr_stop = buy_price - atr_multiplier * prev_atr

    # 期末强制平仓
    sellable_end = max(0, shares - yesterday_buy_shares)
    if shares > 0 and sellable_end > 0:
        revenue = sellable_end * close_price
        commission = max(min_commission, revenue * commission_rate)
        stamp = revenue * stamp_duty_rate
        cost = revenue - commission - stamp

        pnl = cost - sellable_end * buy_price
        pnl_pct = pnl / (sellable_end * buy_price) if buy_price > 0 else 0

        equity += cost
        shares -= sellable_end

        trades.append({
            "buy_date": buy_date, "sell_date": curr["date"], "signal": buy_signal,
            "sell_reason": "期末平仓", "buy_price": buy_price, "sell_price": close_price,
            "pnl_pct": pnl_pct, "pnl": pnl, "equity": equity,
        })

    # ================= 统计输出 =================
    total_trades = len(trades)
    win_trades = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = win_trades / total_trades if total_trades else 0
    avg_pnl_pct = sum(t["pnl_pct"] for t in trades) / total_trades if total_trades else 0
    net_pnl = equity - capital

    print("\n" + "=" * 60)
    print(f"股票代码：{symbol} | ATR周期:{atr_period} | ATR倍数:{atr_multiplier}")
    print(f"初始本金：{capital:,.2f} 元")
    print(f"最终权益：{equity:,.2f} 元")
    print(f"净盈亏：{net_pnl:,.2f} 元")
    print(f"交易次数：{total_trades}")
    print(f"盈利次数：{win_trades}")
    print(f"胜率：{win_rate * 100:.2f}%")
    print(f"平均单笔收益：{avg_pnl_pct * 100:.4f}%")
    print("=" * 60)

    if trades:
        print("\n--- 按卖出原因统计 ---")
        sell_stats = defaultdict(lambda: {"count": 0, "win": 0, "pnl_sum": 0.0})
        for t in trades:
            sell_stats[t["sell_reason"]]["count"] += 1
            if t["pnl"] > 0:
                sell_stats[t["sell_reason"]]["win"] += 1
            sell_stats[t["sell_reason"]]["pnl_sum"] += t["pnl_pct"]

        for reason, st in sell_stats.items():
            wr = st["win"] / st["count"] * 100 if st["count"] else 0
            avg = st["pnl_sum"] / st["count"] * 100 if st["count"] else 0
            print(f"  {reason}：{st['count']}笔  胜率{wr:.1f}%  平均{avg:.4f}%")

        print("\n最近5笔交易：")
        for t in trades[-5:]:
            print(
                f"  {t['buy_date'].date()} {t['signal']} 买{t['buy_price']:.2f} -> "
                f"{t['sell_date'].date()} 卖{t['sell_price']:.2f}({t['sell_reason']}) "
                f"收益 {t['pnl_pct']*100:.2f}% 盈亏 {t['pnl']:.2f}"
            )


if __name__ == "__main__":
    print("开始回测...")
    try:
        backtest_cdp_atr_fixed(
            "sh.605255",
            start_date="2024-01-01",
            atr_period=14,
            atr_multiplier=2.0,
            position_ratio=0.2,
        )
        print("回测结束")
    except Exception as e:
        print(f"出错：{e}")
        traceback.print_exc()