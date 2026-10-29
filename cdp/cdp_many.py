import datetime
import math
import traceback
from collections import defaultdict

import pandas as pd
import baostock as bs
import akshare as ak
import requests
import ccxt  # 引入 CCXT 库
import time  # 引入 time 库用于控制限频




# ==========================================
#  新增：OKX 加密货币数据获取模块
# ==========================================

def fetch_okx_klines(symbol: str, timeframe: str = "1d", limit: int = 1000, since: int = None):
    """
    从 OKX 拉取 K 线数据并转换为标准 DataFrame
    
    参数说明：
    :param symbol: 交易对，如 'BTC/USDT'（必须带斜杠）
    :param timeframe: 时间周期，如 '1d' (日线), '1h' (小时)
    :param limit: 每次拉取的最大数量 (OKX 限制最大 100 条)
    :param since: 起始时间戳 (毫秒)，None 表示拉取最新的 limit 条
    :return: 转换好的 DataFrame
    """
    # 初始化 OKX 交易所实例（开启限频保护，防止 IP 被封）
    exchange = ccxt.okx({
        "enableRateLimit": True,
        # 如果你本地有 Clash/TUN 代理，请取消下面的注释：
        "proxies": {
            "http": "http://127.0.0.1:7890",
            "https": "http://127.0.0.1:7890",
        }
    })

    all_bars = []
    current_since = since

    # 由于 OKX 每次限制 100 条，需要循环分页拉取直到满足 limit 数量
    total_needed = limit
    while total_needed > 0:
        current_limit = min(total_needed, 100)
        bars = exchange.fetch_ohlcv(symbol, timeframe, limit=current_limit, since=current_since)
        
        if not bars:
            break
        
        all_bars.extend(bars)
        if len(bars) < current_limit:
            break
        
        # 取最后一根K线的时间戳，作为下一次拉取的起点
        current_since = bars[-1][0] + 1
        total_needed -= current_limit
        time.sleep(0.5)  # 暂停 0.5 秒，严格遵守交易所限频
    
    if not all_bars:
        return None

    # 转换为标准 Pandas DataFrame
    df = pd.DataFrame(all_bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
    
    # 按时间正序排列
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


# ==========================================
#  如何使用
# ==========================================
if __name__ == "__main__":
    # 测试拉取 BTC/USDT 过去 1000 条日线数据
    print("正在从 OKX 拉取 BTC/USDT 数据...")
    df_crypto = fetch_okx_klines("BTC/USDT", timeframe="1d", limit=1000)
    
    if df_crypto is not None:
        print(f"\n拉取成功！共 {len(df_crypto)} 条数据。")
        print(df_crypto.head())  # 打印前5条查看格式
        print("...")
        print(df_crypto.tail())  # 打印后5条查看格式
    else:
        print("数据拉取失败，请检查网络或代理设置。")

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
    return df \
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
                print(f"  [{code}] 数据源：{name}，共 {len(df)} 行")
                return df
        except Exception as e:
            print(f"  [{code}] {name} 失败：{e}")
    raise RuntimeError("所有数据源都失败")


def compute_cdp(prev_open, prev_high, prev_low, prev_close):
    cdp = (prev_high + prev_low + 2 * prev_close) / 4.0
    nh = 2 * cdp - prev_low
    nl = 2 * cdp - prev_high
    return nh, cdp, nl


def backtest_one(
    symbol: str, start_date: str, end_date: str,
    capital: float, commission_rate: float, min_commission: float,
    stamp_duty_rate: float, atr_period: int, atr_multiplier: float,
    position_ratio: float,
):
    df = fetch_klines(symbol, start_date, end_date)

    df["prev_close"] = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = abs(df["high"] - df["prev_close"])
    tr3 = abs(df["low"] - df["prev_close"])
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(window=atr_period).mean()

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
        prev = df.iloc[i - 1]

        open_price = curr["open"]
        high_price = curr["high"]
        low_price = curr["low"]
        close_price = curr["close"]
        nh = curr["nh"]
        cdp = curr["cdp"]
        prev_h = curr["prev_high"]
        prev_atr = prev["atr"]
        curr_prev_close = curr["prev_close"]

        if any(v is None or (isinstance(v, float) and math.isnan(v))
               for v in [nh, cdp, prev_h, prev_atr]):
            yesterday_buy_shares = 0
            continue

        if curr_prev_close > 0 and abs(open_price / curr_prev_close - 1) > 0.20:
            yesterday_buy_shares = 0
            continue

        sellable = max(0, shares - yesterday_buy_shares)

        if shares > 0 and sellable > 0:
            sell_signal = None
            sell_price = None

            if low_price <= current_atr_stop:
                sell_signal, sell_price = "ATR止损", current_atr_stop
            elif nh > 0 and high_price >= nh:
                sell_signal, sell_price = "NH卖", nh
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
                    "buy_date": buy_date,
                    "sell_date": curr["date"],
                    "signal": buy_signal,
                    "sell_reason": sell_signal,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "pnl_pct": pnl_pct,
                    "pnl": pnl,
                    "equity": equity,
                })
                buy_price, buy_signal, buy_date = 0.0, "", None
                current_atr_stop = 0.0

        yesterday_buy_shares = 0

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
                        current_atr_stop = buy_price - atr_multiplier * prev_atr

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
            "buy_date": buy_date,
            "sell_date": curr["date"],
            "signal": buy_signal,
            "sell_reason": "期末平仓",
            "buy_price": buy_price,
            "sell_price": close_price,
            "pnl_pct": pnl_pct,
            "pnl": pnl,
            "equity": equity,
        })

    total_trades = len(trades)
    win_trades = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = win_trades / total_trades if total_trades else 0.0
    avg_pnl_pct = sum(t["pnl_pct"] for t in trades) / total_trades if total_trades else 0.0
    net_pnl = equity - capital

    sell_stats = defaultdict(lambda: {"count": 0, "win": 0, "pnl_sum": 0.0})
    for t in trades:
        sell_stats[t["sell_reason"]]["count"] += 1
        if t["pnl"] > 0:
            sell_stats[t["sell_reason"]]["win"] += 1
        sell_stats[t["sell_reason"]]["pnl_sum"] += t["pnl_pct"]

    return {
        "symbol": symbol,
        "rows": len(df),
        "capital": capital,
        "equity": equity,
        "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl / capital if capital else 0.0,
        "total_trades": total_trades,
        "win_trades": win_trades,
        "win_rate": win_rate,
        "avg_pnl_pct": avg_pnl_pct,
        "sell_stats": dict(sell_stats),
        "trades": trades,
    }


# 定义一个全局变量，用来保持 BaoStock 连接
_bs_login_state = False 

def fetch_baostock(code: str, start_date: str, end_date: str):
    global _bs_login_state
    
    # 如果还没登录，就执行登录（全程只会执行一次）
    if not _bs_login_state:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"BaoStock 登录失败：{lg.error_msg}")
        _bs_login_state = True

    fields = "date,code,open,high,low,close,volume,amount"
    rs = bs.query_history_k_data_plus(
        code, fields, start_date=start_date, end_date=end_date,
        frequency="d", adjustflag="2",
    )

    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        raise RuntimeError("BaoStock 返回空数据")

    df = pd.DataFrame(rows, columns=rs.fields)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    return df.sort_values("date").reset_index(drop=True)


def backtest_multi(

    symbols=None,
    start_date: str = "2021-01-01",
    end_date: str = None,
    capital: float = 100_000,
    commission_rate: float = 0.00025,
    min_commission: float = 5.0,
    stamp_duty_rate: float = 0.0005,
    atr_period: int = 14,
    atr_multiplier: float = 2.0,
    position_ratio: float = 0.2,
):
    _bs_login_state = False 
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")

    if symbols is None:
        symbols = [
            "sz.002594",   # 比亚迪
            "sh.600519",   # 贵州茅台
            "sz.300750",   # 宁德时代
            "sh.600036",   # 招商银行
            "sz.000858",   # 五粮液
            "sh.601899",   # 紫金矿业
            "sz.300498",   # 温氏股份
            "sh.605255",   # 原基准测试标的
        ]

    print("=" * 80)
    print("多股票批量回测（优化版 - 全局连接）")
    print(f"区间：{start_date} ~ {end_date}")
    print(f"本金：{capital:,.2f} 元 | ATR周期：{atr_period} | ATR倍数：{atr_multiplier} | 仓位：{position_ratio}")
    print(f"股票总数：{len(symbols)} 只")
    print("=" * 80)

    results = []
    total = len(symbols)
    
    for i, symbol in enumerate(symbols):
        try:
            print(f"\n进度：{i+1}/{total} | 正在回测：{symbol}...")
            r = backtest_one(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                capital=capital,
                commission_rate=commission_rate,
                min_commission=min_commission,
                stamp_duty_rate=stamp_duty_rate,
                atr_period=atr_period,
                atr_multiplier=atr_multiplier,
                position_ratio=position_ratio,
            )
            results.append(r)
        except Exception as e:
            print(f"  [警告] 回测 {symbol} 失败：{e}")
            # 遇到错误不中断，继续跑下一只
            continue

    # 全部跑完后，统一退出 BaoStock
    if _bs_login_state:
        bs.logout()
        print("\n已断开 BaoStock 连接。")
        _bs_login_state = False

    if not results:
        print("\n没有成功回测的股票。")
        return results

    print("\n" + "=" * 80)
    print("汇总统计")
    print("=" * 80)

    header = (
        f"{'股票':<12} {'数据行':>6} {'交易次数':>6} {'胜率':>8} "
        f"{'平均单笔':>10} {'净盈亏':>14} {'收益率':>10}"
    )
    print(header)
    print("-" * 80)

    for r in results:
        print(
            f"{r['symbol']:<12} {r['rows']:>6} {r['total_trades']:>6} "
            f"{r['win_rate']*100:>7.2f}% {r['avg_pnl_pct']*100:>9.4f}% "
            f"{r['net_pnl']:>13,.2f} {r['net_pnl_pct']*100:>9.2f}%"
        )

    print("-" * 80)

    total_trades = sum(r["total_trades"] for r in results)
    total_wins = sum(r["win_trades"] for r in results)
    total_net_pnl = sum(r["net_pnl"] for r in results)
    total_capital = sum(r["capital"] for r in results)

    print(
        f"{'合计':<12} {'':>6} {total_trades:>6} "
        f"{(total_wins/total_trades*100) if total_trades else 0:>7.2f}% {'':>10} "
        f"{total_net_pnl:>13,.2f} {(total_net_pnl/total_capital*100) if total_capital else 0:>9.2f}%"
    )

    print("\n--- 按卖出原因统计 ---")
    agg = defaultdict(lambda: {"count": 0, "win": 0, "pnl_sum": 0.0})
    for r in results:
        for reason, st in r["sell_stats"].items():
            agg[reason]["count"] += st["count"]
            agg[reason]["win"] += st["win"]
            agg[reason]["pnl_sum"] += st["pnl_sum"]

    for reason, st in agg.items():
        wr = st["win"] / st["count"] * 100 if st["count"] else 0
        avg = st["pnl_sum"] / st["count"] * 100 if st["count"] else 0
        print(f"  {reason}：{st['count']}笔  胜率{wr:.1f}%  平均{avg:.4f}%")

    return results


def backtest_cdp_atr_fixed(
    symbol: str, start_date: str = "2024-01-01", end_date: str = None,
    capital: float = 100_000, commission_rate: float = 0.00025, min_commission: float = 5.0,
    stamp_duty_rate: float = 0.0005, atr_period: int = 14, atr_multiplier: float = 2.0,
    position_ratio: float = 0.2,
):
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")
    r = backtest_one(
        symbol=symbol, start_date=start_date, end_date=end_date,
        capital=capital, commission_rate=commission_rate, min_commission=min_commission,
        stamp_duty_rate=stamp_duty_rate, atr_period=atr_period, atr_multiplier=atr_multiplier,
        position_ratio=position_ratio,
    )

    equity = r["equity"]
    trades = r["trades"]
    total_trades = r["total_trades"]
    win_trades = r["win_trades"]
    win_rate = r["win_rate"]
    avg_pnl_pct = r["avg_pnl_pct"]
    net_pnl = r["net_pnl"]

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
                f"  {t['buy_date']} {t['signal']} 买{t['buy_price']:.2f} -> "
                f"{t['sell_date']} 卖{t['sell_price']:.2f}({t['sell_reason']}) "
                f"收益 {t['pnl_pct']*100:.2f}% 盈亏 {t['pnl']:.2f}"
            )

    print("\n回测结束")
    return r


# ==========================================
# 【默认开启】多股票批量测试模式
# ==========================================
if __name__ == "__main__":
    # ==========================================
    # 【全市场50只股票压力测试】（默认回测区间：2021-01-01 ~ 至今）
    # ==========================================
    full_test_symbols = [
        # 大消费与医药 (7只)
        "sh.600519", "sz.000858", "sh.600887", "sh.603259", 
        "sh.600276", "sz.300760", "sz.300015", 
        # 科技与高端制造 (15只)
        "sh.603986", "sz.002475", "sz.000938", "sz.000725",
        "sh.600893", "sz.300308", "sh.600519", "sh.601012",
        "sh.600941", "sz.002230", "sz.300750", "sz.002594",
        "sh.600406", "sh.603501", "sh.688981", "sz.300124",
        # 大金融与红利资产 (8只)
        "sh.600036", "sh.601318", "sh.600030", "sh.601857",
        "sh.601088", "sh.600900", "sh.601668", "sh.600019",
        # 周期资源与军工 (10只)
        "sh.601899", "sh.601600", "sh.600111", "sh.600028",
        "sh.600031", "sh.600660", "sh.601816", "sz.300476",
        "sh.600585", "sh.600415",
        # 原基准及波动率样本 (10只)
        "sh.605255", "sz.300498", "sz.002415", "sh.600809",
        "sz.000063", "sz.002049", "sz.002271", "sz.002714",
        "sh.600600", "sz.002698"
    ]
    
    # 直接运行多股票批量测试，起始时间拉到 2021 年
    results = backtest_multi(
        symbols=full_test_symbols, 
        start_date="2021-01-01"
    )

    # 2. 单只股票回测 (如需单独测试，请取消下面这行的注释)
    # backtest_cdp_atr_fixed("sh.605255", start_date="2021-01-01", atr_period=14, atr_multiplier=2.0, position_ratio=0.2)
    # results = backtest_multi(symbols=multi_symbols)

    # 2. 单只股票回测 (如需恢复，请注释掉上面两行，取消下面这行的注释)
    # backtest_cdp_atr_fixed("sh.605255", start_date="2024-01-01", atr_period=14, atr_multiplier=2.0, position_ratio=0.2)