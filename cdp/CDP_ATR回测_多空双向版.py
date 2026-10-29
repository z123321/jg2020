# -*- coding: utf-8 -*-
"""
CDP + ATR 回测系统 —— 开关版（多币种 OKX + A 股可选）
用法：改顶部总控制台的布尔值即可切换。
"""

# ==================== 总控制台 ====================
RUN_OKX = True          # 是否跑 OKX 行情拉取 + 回测
RUN_A_STOCK = False     # 是否跑 A 股 50 只回测
OKX_USE_PROXY = True    # OKX 是否走本地代理（Clash 默认 7890）
OKX_PROXY_PORT = 7890   # 代理端口
OKX_SYMBOLS = ["BTC/USDT", "ETH/USDT", "DOGE/USDT", "SOL/USDT"]  # 要跑的币种列表
OKX_TIMEFRAME = "1d"    # K线周期
OKX_LIMIT = 1000        # 每个币拉多少根
A_STOCK_START_DATE = "2021-01-01"

# 回测参数（A股与币圈共用）
INITIAL_CAPITAL = 100000   # 每标的独立本金
POSITION_RATIO = 0.2       # 每次买入仓位
ATR_MULTIPLIER = 2.8       # ATR 止损倍数（币圈建议 2.5~3.0）
COMMISSION_RATE = 0.001    # 手续费
MIN_COMMISSION = 5         # 最小手续费（A股用，币圈可忽略）
STAMP_DUTY_RATE = 0.001    # 印花税（仅卖出收，币圈设为0）
LOT_SIZE = 100             # 最小下单单位（A股100，币圈0.001）

# ==================== 依赖 ====================
import pandas as pd
import numpy as np
import datetime

# ---------- OKX 依赖 ----------
try:
    import ccxt
    _HAS_CCXT = True
except ImportError:
    _HAS_CCXT = False

# ---------- A 股依赖（RUN_A_STOCK=False 时装不装都行） ----------
try:
    import baostock as bs
    _HAS_BS = True
except ImportError:
    _HAS_BS = False

try:
    import akshare as ak
    _HAS_AK = True
except ImportError:
    _HAS_AK = False

try:
    import requests
    _HAS_REQ = True
except ImportError:
    _HAS_REQ = False

# ==================== 工具函数 ====================

def compute_cdp_atr(df):
    """在 df 上追加 CDP/NH/NL/ATR 列，返回 df"""
    df = df.copy()
    df["prev_close"] = df["close"].shift(1)
    df["prev_high"] = df["high"].shift(1)
    df["prev_low"] = df["low"].shift(1)

    df["CDP"] = (df["prev_high"] + df["prev_low"] + df["prev_close"]) / 3.0
    df["NH"] = 2 * df["CDP"] - df["prev_low"]
    df["NL"] = 2 * df["CDP"] - df["prev_high"]

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["prev_close"]).abs()
    tr3 = (df["low"] - df["prev_close"]).abs()
    df["TR"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = df["TR"].rolling(14).mean()
    return df


def run_backtest_on_df(df, symbol="", lot_size=100, stamp_duty_rate=0.001,
                       min_commission=5, atr_multiplier=2.8,
                       is_t0=False, skip_holiday_gap=True, enable_short=False):
    """
    与数据源无关的回测核心：接收带 CDP/NH/NL/ATR 的 df，返回 (df, trades, stats, reason_stats)
    is_t0=True 时关闭 A 股 T+1 限制（币圈用）
    enable_short=True 时开启做空：
        信号 = 当日盘中跌破 CDP；次日开盘建空仓（若仍持多则先按开盘价平多再反手做空）
        空头止盈 = 价格跌到 NL；空头止损 = 涨破 CDP（方向错判）/ ATR 反向止损
    多空互斥：同一时刻只持一个方向。
    """
    df = df.reset_index(drop=True)
    n = len(df)
    capital = INITIAL_CAPITAL
    position = 0.0
    avg_cost = 0.0
    short_position = 0.0
    short_entry = 0.0
    yesterday_buy_shares = 0.0
    pending_short = False
    trades = []

    for i in range(1, n):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        cdp = row["CDP"]
        nh = row["NH"]
        nl = row["NL"]
        atr = row["ATR"]

        if pd.isna(cdp) or pd.isna(nh) or pd.isna(nl) or pd.isna(atr) or atr <= 0:
            yesterday_buy_shares = position
            continue

        atr_stop = row["open"] - atr_multiplier * atr
        atr_stop_short = row["open"] + atr_multiplier * atr

        # ============ 1) 次日开盘建空仓（消费昨日跌破CDP信号，可反手平多）============
        if enable_short and pending_short and short_position == 0:
            open_px = row["open"]
            # 若持多先平多（反手）
            if position > 0:
                close_shares = position
                commission = max(close_shares * open_px * COMMISSION_RATE, min_commission)
                stamp = close_shares * open_px * stamp_duty_rate
                pnl = (open_px - avg_cost) * close_shares - commission - stamp
                capital += close_shares * open_px - commission - stamp
                trades.append({
                    "symbol": symbol, "buy_date": None, "sell_date": str(row["date"]),
                    "buy_price": avg_cost, "sell_price": open_px, "shares": close_shares,
                    "pnl": pnl, "pnl_pct": (open_px / avg_cost - 1) if avg_cost > 0 else 0,
                    "reason": "反手平多",
                })
                position = 0.0
            max_shares = int((capital * POSITION_RATIO) / open_px / lot_size) * lot_size
            if max_shares > 0:
                commission = max(max_shares * open_px * COMMISSION_RATE, min_commission)
                capital -= commission
                short_position = float(max_shares)
                short_entry = open_px
                trades.append({
                    "symbol": symbol, "buy_date": str(row["date"]), "sell_date": None,
                    "buy_price": open_px, "sell_price": None, "shares": max_shares,
                    "pnl": 0, "pnl_pct": 0, "reason": "做空",
                })
        pending_short = False

        # ============ 2) 多头卖出逻辑 ============
        if position > 0:
            can_sell = position if is_t0 else yesterday_buy_shares
            if can_sell > 0:
                sell_price = None
                reason = None
                if skip_holiday_gap and row["open"] < atr_stop and prev["close"] >= atr_stop:
                    sell_price = row["open"]; reason = "ATR止损"
                elif row["low"] < atr_stop:
                    sell_price = atr_stop; reason = "ATR止损"
                elif row["low"] < cdp:
                    sell_price = cdp; reason = "破CDP止损"
                elif row["high"] > nh:
                    sell_price = nh; reason = "NH卖"

                if sell_price is not None and reason is not None:
                    sell_shares = can_sell
                    commission = max(sell_shares * sell_price * COMMISSION_RATE, min_commission)
                    stamp = sell_shares * sell_price * stamp_duty_rate
                    pnl = (sell_price - avg_cost) * sell_shares - commission - stamp
                    capital += sell_shares * sell_price - commission - stamp
                    position -= sell_shares
                    if not is_t0:
                        yesterday_buy_shares -= sell_shares
                    trades.append({
                        "symbol": symbol, "buy_date": None, "sell_date": str(row["date"]),
                        "buy_price": avg_cost, "sell_price": sell_price, "shares": sell_shares,
                        "pnl": pnl, "pnl_pct": (sell_price / avg_cost - 1) if avg_cost > 0 else 0,
                        "reason": reason,
                    })

        # ============ 3) 空头平仓（止盈NL / 止损涨破CDP / ATR反向止损）============
        if short_position > 0:
            cover_price = None
            reason = None
            if skip_holiday_gap and row["open"] > atr_stop_short and prev["close"] <= atr_stop_short:
                cover_price = row["open"]; reason = "ATR止损(空)"
            elif row["high"] > atr_stop_short:
                cover_price = atr_stop_short; reason = "ATR止损(空)"
            elif row["high"] > cdp:
                cover_price = cdp; reason = "破CDP止损(空)"
            elif row["low"] < nl:
                cover_price = nl; reason = "NL止盈"

            if cover_price is not None and reason is not None:
                cover_shares = short_position
                commission = max(cover_shares * cover_price * COMMISSION_RATE, min_commission)
                stamp = cover_shares * cover_price * stamp_duty_rate
                pnl = (short_entry - cover_price) * cover_shares - commission - stamp
                capital += pnl
                short_position = 0.0
                trades.append({
                    "symbol": symbol, "buy_date": None, "sell_date": str(row["date"]),
                    "buy_price": short_entry, "sell_price": cover_price, "shares": cover_shares,
                    "pnl": pnl, "pnl_pct": (short_entry - cover_price) / short_entry if short_entry > 0 else 0,
                    "reason": reason,
                })

        # ============ 4) 多头买入（做空开启时，空仓且无做空信号才开多）============
        long_ok = True
        if enable_short:
            long_ok = (short_position == 0) and (position == 0) and (row["close"] >= cdp)
        if position == 0 and long_ok and row["high"] > cdp:
            buy_price = cdp
            max_shares = int((capital * POSITION_RATIO) / buy_price / lot_size) * lot_size
            if max_shares > 0:
                commission = max(max_shares * buy_price * COMMISSION_RATE, min_commission)
                cost = max_shares * buy_price + commission
                if cost <= capital:
                    capital -= cost
                    position = float(max_shares)
                    avg_cost = buy_price
                    trades.append({
                        "symbol": symbol, "buy_date": str(row["date"]), "sell_date": None,
                        "buy_price": buy_price, "sell_price": None, "shares": max_shares,
                        "pnl": 0, "pnl_pct": 0, "reason": "买入",
                    })

        # ============ 5) 记录次日建空信号：当日盘中跌破 CDP ============
        if enable_short and row["close"] < cdp:
            pending_short = True

        yesterday_buy_shares = position

    # ============ 期末平仓（多头 + 空头）============
    last = df.iloc[-1]
    if position > 0:
        sell_price = last["close"]
        commission = max(position * sell_price * COMMISSION_RATE, min_commission)
        stamp = position * sell_price * stamp_duty_rate
        pnl = (sell_price - avg_cost) * position - commission - stamp
        capital += position * sell_price - commission - stamp
        trades.append({
            "symbol": symbol, "buy_date": None, "sell_date": str(last["date"]),
            "buy_price": avg_cost, "sell_price": sell_price, "shares": position,
            "pnl": pnl, "pnl_pct": (sell_price / avg_cost - 1) if avg_cost > 0 else 0,
            "reason": "期末平仓",
        })
        position = 0.0
    if short_position > 0:
        cover_price = last["close"]
        commission = max(short_position * cover_price * COMMISSION_RATE, min_commission)
        stamp = short_position * cover_price * stamp_duty_rate
        pnl = (short_entry - cover_price) * short_position - commission - stamp
        capital += pnl
        trades.append({
            "symbol": symbol, "buy_date": None, "sell_date": str(last["date"]),
            "buy_price": short_entry, "sell_price": cover_price, "shares": short_position,
            "pnl": pnl, "pnl_pct": (short_entry - cover_price) / short_entry if short_entry > 0 else 0,
            "reason": "期末平仓(空)",
        })
        short_position = 0.0

    # 统计：开仓记录不计入平仓统计
    open_reasons = ("买入", "做空")
    sell_trades = [t for t in trades if t["reason"] not in open_reasons]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    total_pnl = capital - INITIAL_CAPITAL
    ret = total_pnl / INITIAL_CAPITAL * 100
    avg_pnl_pct = np.mean([t["pnl_pct"] for t in sell_trades]) * 100 if sell_trades else 0
    win_rate = len(wins) / len(sell_trades) * 100 if sell_trades else 0

    stats = {
        "symbol": symbol, "rows": n, "trades": len(sell_trades),
        "win_rate": win_rate, "avg_pnl_pct": avg_pnl_pct,
        "total_pnl": total_pnl, "return_pct": ret,
    }

    reason_list = ["破CDP止损", "NH卖", "ATR止损", "期末平仓",
                   "反手平多", "破CDP止损(空)", "NL止盈", "ATR止损(空)", "期末平仓(空)"]
    reason_stats = []
    for reason in reason_list:
        rt = [t for t in sell_trades if t["reason"] == reason]
        if not rt:
            continue
        rw = [t for t in rt if t["pnl"] > 0]
        reason_stats.append({
            "reason": reason, "count": len(rt),
            "win_rate": len(rw) / len(rt) * 100,
            "avg_pct": np.mean([t["pnl_pct"] for t in rt]) * 100,
        })

    return df, trades, stats, reason_stats


def print_summary(all_stats, all_reason_stats):
    """打印多标的对比汇总表 + 按原因统计"""
    sep = "=" * 80
    print(sep)
    print("汇总统计")
    print(sep)
    header = f"{'标的':<16}{'数据行':>7}{'交易次数':>9}{'胜率':>10}{'平均单笔':>12}{'净盈亏':>14}{'收益率':>10}"
    print(header)
    print("-" * len(header))
    for s in all_stats:
        print(f"{s['symbol']:<14}{s['rows']:>7}{s['trades']:>9}"
              f"{s['win_rate']:>9.2f}%{s['avg_pnl_pct']:>+11.4f}%"
              f"{s['total_pnl']:>+13,.2f}{s['return_pct']:>+9.2f}%")
    print("-" * len(header))
    total_trades = sum(s["trades"] for s in all_stats)
    total_pnl = sum(s["total_pnl"] for s in all_stats)
    total_ret = total_pnl / INITIAL_CAPITAL * 100 if all_stats else 0
    print(f"{'合计':<14}{total_trades:>16}{total_pnl:>+13,.2f}{total_ret:>+9.2f}%")
    print()
    print("--- 按卖出原因统计 ---")
    for r in all_reason_stats:
        print(f"  {r['reason']}：{r['count']}笔  胜率{r['win_rate']:.1f}%  平均{r['avg_pct']:+.4f}%")


# ==================== OKX 数据源 ====================

def fetch_okx_klines(symbol, timeframe="1d", limit=1000, use_proxy=False, proxy_port=7890):
    if not _HAS_CCXT:
        print("❌ 未安装 ccxt，请先 pip install ccxt")
        return None
    exchange = ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    if use_proxy:
        exchange.proxies = {"http": f"http://127.0.0.1:{proxy_port}",
                            "https": f"http://127.0.0.1:{proxy_port}"}

    print(f"正在从 OKX 拉取 {symbol} 数据...")
    all_klines = []
    current_since = None
    page_size = 100

    # 估算 timeframe 毫秒数，倒推起始时间戳
    tf_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
             "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}
    tf = tf_ms.get(timeframe, 86_400_000)
    if current_since is None:
        current_since = exchange.milliseconds() - tf * (limit + 20)

    seen_ts = set()
    while len(all_klines) < limit:
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe, since=current_since, limit=page_size)
        except Exception as e:
            print(f"  ⚠ 拉取失败: {e}")
            break
        if not raw:
            break
        new = False
        for k in raw:
            ts = k[0]
            if ts not in seen_ts:
                seen_ts.add(ts)
                all_klines.append(k)
                new = True
        if not new:
            break
        last_ts = raw[-1][0]
        current_since = last_ts + 1
        if len(raw) < page_size:
            break

    if not all_klines:
        print("  ❌ 未拉到任何数据")
        return None

    all_klines.sort(key=lambda x: x[0])
    all_klines = all_klines[-limit:]
    df = pd.DataFrame(all_klines, columns=["ts", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.strftime("%Y-%m-%d")
    df = df[["date", "open", "high", "low", "close", "volume"]]
    print(f"拉取成功！共 {len(df)} 条数据。")
    return df


# ==================== A 股数据源（保留，开关控制） ====================

_bs_login_state = False

def fetch_baostock(stock_code, start_date, end_date=None):
    global _bs_login_state
    if not _HAS_BS:
        print(f"  ⚠ 未安装 baostock，跳过 {stock_code}")
        return None
    if not _bs_login_state:
        lg = bs.login()
        if lg.error_code != "0":
            print(f"  ❌ BaoStock 登录失败: {lg.error_msg}")
            return None
        _bs_login_state = True
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")
    fields = "date,open,high,low,close,volume"
    rs = bs.query_history_k_data_plus(stock_code, fields, start_date=start_date,
                                       end_date=end_date, frequency="d", adjustflag="3")
    if rs.error_code != "0":
        print(f"  ❌ {stock_code} 查询失败: {rs.error_msg}")
        return None
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=rs.fields)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"])
    return df[["date", "open", "high", "low", "close", "volume"]]


def fetch_akshare(stock_code, start_date, end_date=None):
    if not _HAS_AK:
        return None
    if end_date is None:
        end_date = datetime.date.today().strftime("%Y-%m-%d")
    try:
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily",
                                 start_date=start_date.replace("-", ""),
                                 end_date=end_date.replace("-", ""), adjust="qfq")
    except Exception:
        return None
    if df.empty:
        return None
    df = df.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                             "最低": "low", "收盘": "close", "成交量": "volume"})
    df["volume"] = df["volume"] * 100
    return df[["date", "open", "high", "low", "close", "volume"]]


def fetch_sina(stock_code, start_date, end_date=None):
    if not _HAS_REQ:
        return None
    market = "sh" if stock_code.startswith("6") else "sz"
    url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    try:
        resp = requests.get(url, params={"symbol": f"{market}{stock_code}",
                                          "scale": "240", "datalen": "1000"}, timeout=10)
        data = resp.json()
    except Exception:
        return None
    if not data:
        return None
    rows = []
    for r in data:
        d = r["day"].split()[0] if " " in str(r["day"]) else str(r["day"])
        if d < start_date:
            continue
        rows.append({"date": d, "open": float(r["open"]), "high": float(r["high"]),
                       "low": float(r["low"]), "close": float(r["close"]),
                       "volume": float(r["volume"])})
    return pd.DataFrame(rows) if rows else None


def fetch_stock_data(stock_code, start_date, end_date=None):
    for name, fn in [("BaoStock", fetch_baostock), ("AKShare", fetch_akshare), ("Sina", fetch_sina)]:
        df = fn(stock_code, start_date, end_date)
        if df is not None and len(df) > 30:
            return df
    return None


# ==================== 回测入口 ====================

def backtest_one(stock_code, start_date, end_date=None, source="auto"):
    df = fetch_stock_data(stock_code, start_date, end_date)
    if df is None or len(df) < 60:
        print(f"  ⚠ {stock_code} 数据不足，跳过")
        return None, [], {}, []
    df = compute_cdp_atr(df)
    return run_backtest_on_df(df, symbol=stock_code, lot_size=LOT_SIZE,
                               stamp_duty_rate=STAMP_DUTY_RATE,
                               min_commission=MIN_COMMISSION,
                               atr_multiplier=ATR_MULTIPLIER,
                               is_t0=False, skip_holiday_gap=True)


def backtest_multi(stock_list, start_date, end_date=None):
    global _bs_login_state
    all_stats = []
    all_reason_stats = []
    for code in stock_list:
        print(f"\n--- {code} ---")
        _, _, stats, reason_stats = backtest_one(code, start_date, end_date)
        if stats:
            all_stats.append(stats)
            all_reason_stats.extend(reason_stats)
    if all_stats:
        print_summary(all_stats, all_reason_stats)
    if _bs_login_state:
        bs.logout()
        _bs_login_state = False
    return all_stats


# ==================== OKX 多币种回测入口 ====================

def run_okx_multi():
    if not _HAS_CCXT:
        print("❌ 未安装 ccxt，请先 pip install ccxt")
        return
    all_stats = []
    all_reason_stats = []
    for sym in OKX_SYMBOLS:
        print(f"\n{'='*80}")
        df = fetch_okx_klines(sym, timeframe=OKX_TIMEFRAME, limit=OKX_LIMIT,
                               use_proxy=OKX_USE_PROXY, proxy_port=OKX_PROXY_PORT)
        if df is None or len(df) < 60:
            print(f"  ⚠ {sym} 数据不足，跳过\n")
            continue
        # 打印行情预览
        print(df.head().to_string(index=False))
        print("...")
        print(df.tail().to_string(index=False))

        df = compute_cdp_atr(df)
        _, _, stats, reason_stats = run_backtest_on_df(
            df, symbol=sym, lot_size=0.001, stamp_duty_rate=0,
            min_commission=0.1,
            atr_multiplier=ATR_MULTIPLIER,
            is_t0=True, skip_holiday_gap=False,
            enable_short=True,
        )
        if stats:
            all_stats.append(stats)
            all_reason_stats.extend(reason_stats)

    print()
    print("=" * 80)
    print(f"OKX 加密货币 CDP/ATR 回测（T+0 / 无印花税 / ATR倍数={ATR_MULTIPLIER} / 最小单位=0.001）")
    print(f"本金：{INITIAL_CAPITAL:,} USDT")
    print("=" * 80)
    print()
    if all_stats:
        print_summary(all_stats, all_reason_stats)
    else:
        print("⚠ 没有可回测的币种数据")


# ==================== A 股回测入口 ====================

def run_a_stock_demo():
    stock_list = [
        "sh.600000", "sh.600036", "sh.600519", "sh.601318", "sh.600887",
        "sz.000001", "sz.000333", "sz.000651", "sz.000858", "sz.002415",
        "sh.600030", "sh.600276", "sh.600436", "sh.600563", "sh.600809",
        "sz.000002", "sz.000568", "sz.000596", "sz.000661", "sz.000725",
        "sh.600016", "sh.600028", "sh.600031", "sh.600050", "sh.600104",
        "sz.000063", "sz.000100", "sz.000157", "sz.000301", "sz.000425",
        "sh.600111", "sh.600150", "sh.600196", "sh.600219", "sh.600309",
        "sz.000538", "sz.000625", "sz.000776", "sz.000895", "sz.001979",
        "sh.600346", "sh.600406", "sh.600438", "sh.600585", "sh.600690",
        "sz.000963", "sz.002001", "sz.002027", "sz.002049", "sz.002142",
    ]
    print("=" * 80)
    print("A 股 CDP/ATR 回测（T+1 / 印花税0.1% / ATR倍数=2.8 / 最小单位=100股）")
    print("=" * 80)
    backtest_multi(stock_list, start_date=A_STOCK_START_DATE)


# ==================== 主程序 ====================

if __name__ == "__main__":
    if RUN_OKX:
        run_okx_multi()
    if RUN_A_STOCK:
        run_a_stock_demo()
    if not RUN_OKX and not RUN_A_STOCK:
        print("⚠ 当前 RUN_OKX 和 RUN_A_STOCK 均为 False，未执行任何回测。")