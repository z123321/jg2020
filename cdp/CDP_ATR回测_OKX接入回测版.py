# -*- coding: utf-8 -*-
import datetime
import math
import time
from collections import defaultdict

import pandas as pd
import ccxt  # 引入 CCXT 库

# A股相关依赖：仅在 RUN_A_STOCK=True 时才需要；这里做容错，缺包不影响跑 OKX
try:
    import baostock as bs
    _HAS_BAOSTOCK = True
except Exception:
    bs = None
    _HAS_BAOSTOCK = False
try:
    import akshare as ak
    _HAS_AKSHARE = True
except Exception:
    ak = None
    _HAS_AKSHARE = False
try:
    import requests
except Exception:
    requests = None


# ==========================================================
#  【总控制台】所有运行模式集中在这里，改这几个值即可切换
#  代码一行都不删，靠开关决定哪些模块真正执行。
# ==========================================================
RUN_OKX     = True    # 开关1：是否运行 OKX 加密货币回测（True=跑，False=不跑）
RUN_A_STOCK = False   # 开关2：是否运行 A股50只股票批量回测（True=跑，False=不跑）

# OKX 运行参数
OKX_SYMBOL     = "BTC/USDT"     # 交易对，CCXT 统一格式，必须带斜杠
OKX_TIMEFRAME  = "1d"           # K线周期：1d 日线 / 1h 小时 / 4h 等
OKX_LIMIT      = 1000           # 拉取根数
OKX_USE_PROXY  = True           # 是否走本地代理（clash 一般是 127.0.0.1:7890）
OKX_PROXY_ADDR = "http://127.0.0.1:7890"

# OKX 回测参数（已针对币圈特性适配）
OKX_CAPITAL          = 100_000   # 初始本金(USDT)
OKX_COMMISSION_RATE  = 0.001     # 手续费率(OKX taker 约 0.1%)
OKX_MIN_COMMISSION   = 0.1       # 最低手续费
OKX_ATR_PERIOD       = 14
OKX_ATR_MULTIPLIER   = 2.8       # 币圈波动大，ATR 倍数放大到 2.5~3.0
OKX_POSITION_RATIO   = 0.2       # 单笔仓位比例
OKX_LOT_SIZE         = 0.001     # 最小下单单位(个币)，币圈可小数；A股是100(一手)

# A股回测参数
A_STOCK_START_DATE = "2021-01-01"


# ==========================================
#  OKX 加密货币数据获取模块
# ==========================================
def fetch_okx_klines(symbol, timeframe="1d", limit=1000, since=None):
    """
    从 OKX 拉取 K 线数据并转换为标准 DataFrame。
    since=None 时自动倒推起始时间戳，分页方向为"从过去到现在"，能拉满 limit 根。
    """
    okx_conf = {"enableRateLimit": True}
    if OKX_USE_PROXY:
        okx_conf["proxies"] = {"http": OKX_PROXY_ADDR, "https": OKX_PROXY_ADDR}
    exchange = ccxt.okx(okx_conf)

    if since is None:
        tf_ms = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
                 "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
                 "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}.get(timeframe, 86_400_000)
        current_since = exchange.milliseconds() - tf_ms * (limit + 10)
    else:
        current_since = since

    all_bars = []
    total_needed = limit
    while total_needed > 0:
        current_limit = min(total_needed, 100)
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe, limit=current_limit, since=current_since)
        except Exception as e:
            print(f"  [OKX] fetch_ohlcv 异常，停止拉取：{e}")
            break
        if not bars:
            break
        all_bars.extend(bars)
        if len(bars) < current_limit:
            break
        current_since = bars[-1][0] + 1   # 向未来推进起始戳（从过去往现在翻）
        total_needed -= current_limit
        time.sleep(0.3)

    if not all_bars:
        return None

    df = pd.DataFrame(all_bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


# ==========================================================
#  回测核心引擎（与数据源无关，A股/币圈共用同一套 CDP/ATR 逻辑）
#  参数化差异：T+1限制 / 印花税 / 最小下单单位 / 跳空过滤
# ==========================================================
def compute_cdp(prev_open, prev_high, prev_low, prev_close):
    cdp = (prev_high + prev_low + 2 * prev_close) / 4.0
    nh = 2 * cdp - prev_low
    nl = 2 * cdp - prev_high
    return nh, cdp, nl


def run_backtest_on_df(
    df, symbol_label,
    capital, commission_rate, min_commission,
    stamp_duty_rate, atr_period, atr_multiplier, position_ratio,
    t_plus_1=True,     # A股=True(T+1)；币圈=False(T+0)
    lot_size=100,      # A股=100(一手)；币圈=0.001(可小数)
    gap_filter=0.20,   # 跳空阈值，None=不做跳空过滤(币圈)
):
    df = df.copy().reset_index(drop=True)

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
            row["prev_open"], row["prev_high"], row["prev_low"], row["prev_close"])
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
    close_price = df.iloc[-1]["close"]

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

        # 跳空过滤：A股防极端跳空滑价；币圈 gap_filter=None 时跳过此过滤
        if gap_filter is not None and curr_prev_close > 0 and abs(open_price / curr_prev_close - 1) > gap_filter:
            yesterday_buy_shares = 0
            continue

        # T+1：当日买入不可卖；T+0(币圈) sellable=shares
        sellable = max(0, shares - yesterday_buy_shares) if t_plus_1 else shares

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
                stamp = revenue * stamp_duty_rate   # 币圈传 0 即无印花税
                cost = revenue - commission - stamp

                pnl = cost - sellable * buy_price
                pnl_pct = pnl / (sellable * buy_price) if buy_price > 0 else 0

                equity += cost
                shares -= sellable

                trades.append({
                    "buy_date": buy_date, "sell_date": curr["date"],
                    "signal": buy_signal, "sell_reason": sell_signal,
                    "buy_price": buy_price, "sell_price": sell_price,
                    "pnl_pct": pnl_pct, "pnl": pnl, "equity": equity})
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
                if lot_size and lot_size >= 1:
                    raw_shares = int(use_capital / target_price / lot_size) * lot_size
                else:
                    step = lot_size if lot_size else 1
                    raw_shares = math.floor(use_capital / target_price / step) * step

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

    # 期末平仓
    sellable_end = max(0, shares - yesterday_buy_shares) if t_plus_1 else shares
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
            "buy_date": buy_date, "sell_date": curr["date"],
            "signal": buy_signal, "sell_reason": "期末平仓",
            "buy_price": buy_price, "sell_price": close_price,
            "pnl_pct": pnl_pct, "pnl": pnl, "equity": equity})

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
        "symbol": symbol_label, "rows": len(df), "capital": capital,
        "equity": equity, "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl / capital if capital else 0.0,
        "total_trades": total_trades, "win_trades": win_trades,
        "win_rate": win_rate, "avg_pnl_pct": avg_pnl_pct,
        "sell_stats": dict(sell_stats), "trades": trades}


# ==========================================================
#  打印"汇总统计"表（A股多标的 / OKX 币种 通用）
# ==========================================================
def print_summary(results, unit="元"):
    if not results:
        print("\n没有成功回测的标的。")
        return
    print("\n" + "=" * 80)
    print("汇总统计")
    print("=" * 80)
    header = (
        f"{'标的':<14} {'数据行':>6} {'交易次数':>6} {'胜率':>8} "
        f"{'平均单笔':>10} {'净盈亏':>14} {'收益率':>10}")
    print(header)
    print("-" * 80)
    for r in results:
        print(
            f"{r['symbol']:<14} {r['rows']:>6} {r['total_trades']:>6} "
            f"{r['win_rate']*100:>7.2f}% {r['avg_pnl_pct']*100:>9.4f}% "
            f"{r['net_pnl']:>13,.2f} {r['net_pnl_pct']*100:>9.2f}%")
    print("-" * 80)

    total_trades = sum(r["total_trades"] for r in results)
    total_wins = sum(r["win_trades"] for r in results)
    total_net_pnl = sum(r["net_pnl"] for r in results)
    total_capital = sum(r["capital"] for r in results)
    print(
        f"{'合计':<14} {'':>6} {total_trades:>6} "
        f"{(total_wins/total_trades*100) if total_trades else 0:>7.2f}% {'':>10} "
        f"{total_net_pnl:>13,.2f} {(total_net_pnl/total_capital*100) if total_capital else 0:>9.2f}%")

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


# ==========================================
#  OKX 模式入口：拉行情 + 接回测引擎 + 输出统计
# ==========================================
def run_okx_demo():
    print("正在从 OKX 拉取 %s 数据..." % OKX_SYMBOL)
    try:
        df_crypto = fetch_okx_klines(OKX_SYMBOL, timeframe=OKX_TIMEFRAME, limit=OKX_LIMIT)
    except Exception as e:
        print("OKX 拉取过程抛出异常：%s" % e)
        return None

    if df_crypto is None:
        print("数据拉取失败，请检查网络或代理设置（当前代理开关 OKX_USE_PROXY=%s）。" % OKX_USE_PROXY)
        return None

    print("\n拉取成功！共 %d 条数据。" % len(df_crypto))
    print(df_crypto.head())
    print("...")
    print(df_crypto.tail())

    # === 关键：把 OKX 的 df 喂进回测引擎，产出统计数据 ===
    print("\n" + "=" * 80)
    print("OKX 加密货币 CDP/ATR 回测（T+0 / 无印花税 / ATR倍数=%.1f / 最小单位=%s）"
          % (OKX_ATR_MULTIPLIER, OKX_LOT_SIZE))
    print("标的：%s | 周期：%s | 本金：%s USDT" % (OKX_SYMBOL, OKX_TIMEFRAME, format(OKX_CAPITAL, ",")))
    print("=" * 80)

    r = run_backtest_on_df(
        df=df_crypto, symbol_label=OKX_SYMBOL,
        capital=OKX_CAPITAL, commission_rate=OKX_COMMISSION_RATE,
        min_commission=OKX_MIN_COMMISSION, stamp_duty_rate=0.0,  # 币圈无印花税
        atr_period=OKX_ATR_PERIOD, atr_multiplier=OKX_ATR_MULTIPLIER,
        position_ratio=OKX_POSITION_RATIO,
        t_plus_1=False,          # 币圈 T+0
        lot_size=OKX_LOT_SIZE,   # 可小数下单
        gap_filter=None,         # 币圈不做跳空过滤
    )
    print_summary([r], unit="USDT")
    return r


# ==========================================
#  A股数据源与回测（保持原逻辑，仅改为复用统一引擎）
# ==========================================
_bs_login_state = False


def fetch_baostock(code, start_date, end_date):
    global _bs_login_state
    if not _HAS_BAOSTOCK:
        raise RuntimeError("未安装 baostock，无法拉取 A 股数据")
    if not _bs_login_state:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"BaoStock 登录失败：{lg.error_msg}")
        _bs_login_state = True

    fields = "date,code,open,high,low,close,volume,amount"
    rs = bs.query_history_k_data_plus(
        code, fields, start_date=start_date, end_date=end_date,
        frequency="d", adjustflag="2")
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


def fetch_akshare(code, start_date, end_date):
    if not _HAS_AKSHARE:
        raise RuntimeError("未安装 akshare")
    m = code.split(".")
    if len(m) != 2:
        raise ValueError(f"代码格式错误：{code}")
    market, raw = m
    symbol = f"1{raw}" if market == "sh" else f"0{raw}" if market == "sz" else None
    if not symbol:
        raise ValueError(f"不支持的市场：{market}")
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=start_date.replace("-", ""), end_date=end_date.replace("-", ""), adjust="qfq")
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
        "收盘": "close", "成交量": "volume", "成交额": "amount"})
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def fetch_sina(code, start_date, end_date):
    if requests is None:
        raise RuntimeError("未安装 requests")
    m = code.split(".")
    if len(m) != 2:
        raise ValueError(f"代码格式错误：{code}")
    market, raw = m
    symbol = f"sh{raw}" if market == "sh" else f"sz{raw}" if market == "sz" else None
    if not symbol:
        raise ValueError(f"不支持的市场：{market}")
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    resp = requests.get(url, params={"symbol": symbol, "scale": "240", "ma": "no", "datalen": "5000"}, timeout=15)
    resp.raise_for_status()
    rows = []
    for item in resp.json():
        try:
            dt = datetime.datetime.strptime(item.get("day", ""), "%Y-%m-%d")
        except Exception:
            continue
        if pd.Timestamp(start_date) <= dt <= pd.Timestamp(end_date):
            rows.append({"date": dt, "open": float(item["open"]), "high": float(item["high"]),
                         "low": float(item["low"]), "close": float(item["close"]),
                         "volume": float(item["volume"]), "amount": float(item.get("amount", 0))})
    if not rows:
        raise RuntimeError("新浪财经返回空数据")
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def fetch_klines(code, start_date, end_date):
    sources = [("BaoStock", fetch_baostock), ("AKShare", fetch_akshare), ("新浪财经", fetch_sina)]
    for name, fn in sources:
        try:
            df = fn(code, start_date, end_date)
            if df is not None and not df.empty and len(df) >= 30:
                print(f"  [{code}] 数据源：{name}，共 {len(df)} 行")
                return df
        except Exception as e:
            print(f"  [{code}] {name} 失败：{e}")
    raise RuntimeError("所有数据源都失败")


def backtest_one(symbol, start_date, end_date, capital, commission_rate, min_commission,
                 stamp_duty_rate, atr_period, atr_multiplier, position_ratio):
    df = fetch_klines(symbol, start_date, end_date)
    # A股：T+1、有印花税、一手=100、保留跳空过滤
    return run_backtest_on_df(
        df=df, symbol_label=symbol, capital=capital, commission_rate=commission_rate,
        min_commission=min_commission, stamp_duty_rate=stamp_duty_rate,
        atr_period=atr_period, atr_multiplier=atr_multiplier, position_ratio=position_ratio,
        t_plus_1=True, lot_size=100, gap_filter=0.20)


def backtest_multi(symbols=None, start_date="2021-01-01", end_date=None, capital=100_000,
                   commission_rate=0.00025, min_commission=5.0, stamp_duty_rate=0.0005,
                   atr_period=14, atr_multiplier=2.0, position_ratio=0.2):
    global _bs_login_state
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")
    if symbols is None:
        symbols = ["sz.002594", "sh.600519", "sz.300750", "sh.600036",
                   "sz.000858", "sh.601899", "sz.300498", "sh.605255"]
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
            r = backtest_one(symbol=symbol, start_date=start_date, end_date=end_date,
                             capital=capital, commission_rate=commission_rate,
                             min_commission=min_commission, stamp_duty_rate=stamp_duty_rate,
                             atr_period=atr_period, atr_multiplier=atr_multiplier,
                             position_ratio=position_ratio)
            results.append(r)
        except Exception as e:
            print(f"  [警告] 回测 {symbol} 失败：{e}")
            continue
    if _bs_login_state:
        bs.logout()
        print("\n已断开 BaoStock 连接。")
        _bs_login_state = False
    print_summary(results, unit="元")
    return results


def backtest_cdp_atr_fixed(symbol, start_date="2024-01-01", end_date=None, capital=100_000,
                           commission_rate=0.00025, min_commission=5.0, stamp_duty_rate=0.0005,
                           atr_period=14, atr_multiplier=2.0, position_ratio=0.2):
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")
    r = backtest_one(symbol=symbol, start_date=start_date, end_date=end_date, capital=capital,
                     commission_rate=commission_rate, min_commission=min_commission,
                     stamp_duty_rate=stamp_duty_rate, atr_period=atr_period,
                     atr_multiplier=atr_multiplier, position_ratio=position_ratio)
    print("\n" + "=" * 60)
    print(f"股票代码：{symbol} | ATR周期:{atr_period} | ATR倍数:{atr_multiplier}")
    print(f"初始本金：{capital:,.2f} 元 | 最终权益：{r['equity']:,.2f} 元 | 净盈亏：{r['net_pnl']:,.2f} 元")
    print(f"交易次数：{r['total_trades']} | 胜率：{r['win_rate']*100:.2f}% | 平均单笔：{r['avg_pnl_pct']*100:.4f}%")
    print("=" * 60)
    return r


def run_a_stock_backtest():
    full_test_symbols = [
        "sh.600519", "sz.000858", "sh.600887", "sh.603259", "sh.600276", "sz.300760", "sz.300015",
        "sh.603986", "sz.002475", "sz.000938", "sz.000725", "sh.600893", "sz.300308", "sh.601012",
        "sh.600941", "sz.002230", "sz.300750", "sz.002594", "sh.600406", "sh.603501", "sh.688981", "sz.300124",
        "sh.600036", "sh.601318", "sh.600030", "sh.601857", "sh.601088", "sh.600900", "sh.601668", "sh.600019",
        "sh.601899", "sh.601600", "sh.600111", "sh.600028", "sh.600031", "sh.600660", "sh.601816", "sz.300476",
        "sh.600585", "sh.600415", "sh.605255", "sz.300498", "sz.002415", "sh.600809",
        "sz.000063", "sz.002049", "sz.002271", "sz.002714", "sh.600600", "sz.002698"]
    return backtest_multi(symbols=full_test_symbols, start_date=A_STOCK_START_DATE)


# ==========================================
#  【唯一主入口】整份文件只有一个 __main__，按开关分发
# ==========================================
if __name__ == "__main__":
    print("当前运行配置：RUN_OKX=%s | RUN_A_STOCK=%s" % (RUN_OKX, RUN_A_STOCK))
    print("-" * 80)
    if RUN_OKX:
        try:
            run_okx_demo()
        except Exception as e:
            print("[OKX] 模式运行异常，已捕获，不影响其它模式：%s" % e)
    if RUN_A_STOCK:
        try:
            run_a_stock_backtest()
        except Exception as e:
            print("[A股] 模式运行异常：%s" % e)
    if not RUN_OKX and not RUN_A_STOCK:
        print("两个开关都关闭了，没有可运行的任务。请至少打开一个开关。")
