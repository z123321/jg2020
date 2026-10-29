# -*- coding: utf-8 -*-
"""
CDP + ATR 回测系统 —— 加密货币(OKX)版
从开关版拆分而来：仅保留 OKX 行情拉取 + 币圈多空回测。
依赖：pip install ccxt pandas numpy
"""

# ==================== 控制台 ====================
OKX_USE_PROXY = True    # OKX 是否走本地代理（Clash 默认 7890）
OKX_PROXY_PORT = 7890   # 代理端口
OKX_SYMBOLS = ["BTC/USDT", "ETH/USDT", "DOGE/USDT", "SOL/USDT"]  # 要跑的币种列表
OKX_TIMEFRAME = "1d"    # K线周期
OKX_LIMIT = 1000        # 每个币拉多少根

# ==================== 回测参数（币圈） ====================
INITIAL_CAPITAL = 100000   # 每标的独立本金(USDT)
POSITION_RATIO = 0.2       # 每次买入仓位
ATR_MULTIPLIER = 2.8       # ATR 止损倍数（币圈建议 2.5~3.0）
COMMISSION_RATE = 0.001    # 手续费
MIN_COMMISSION = 0.1       # 最小手续费（币圈）
STAMP_DUTY_RATE = 0        # 印花税（币圈无）
LOT_SIZE = 0.001           # 最小下单单位（币圈）
SLIPPAGE = 0.0005          # 单边滑点(0.05%)，成交价向不利方向偏移
COOLDOWN_BARS = 3          # 同向冷却：止损/离场后N根K线内不再同向开仓(压过度交易)

# ==================== 依赖 ====================
import pandas as pd
import numpy as np
import datetime

try:
    import ccxt
    _HAS_CCXT = True
except ImportError:
    _HAS_CCXT = False


# ==================== OKX 模拟盘实单配置 ====================
ENABLE_LIVE_TRADING = False          # True=启用模拟盘下单（回测同时发单到OKX Demo）
OKX_DEMO_MODE = True                 # True=走OKX Demo Trading模拟盘（不动真钱）
OKX_API_KEY = ""                     # OKX API Key（网页-交易-模拟交易-API创建）
OKX_API_SECRET = ""                  # OKX API Secret
OKX_API_PASSPHRASE = ""              # OKX API Passphrase
TRADE_MARKET = "swap"                # "spot"现货 / "swap"永续合约（做空需swap）
LIVE_TRADE_RECENT_BARS = 30          # 只有最近N根K线的信号才实盘下单，避免历史信号刷爆账户

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
    # atr_ref = 上一根已收盘K线的ATR，作本根止损基准（当日ATR含当日高低价属未来信息，不能用）
    df["atr_ref"] = df["ATR"].shift(1)
    return df


def run_backtest_on_df(df, symbol="", lot_size=0.001, stamp_duty_rate=0.0,
                       min_commission=0.1, atr_multiplier=2.8,
                       is_t0=True, skip_holiday_gap=False, enable_short=True,
                       slippage=0.0005, cooldown_bars=3):
    # 修正后回测核心：去未来函数 / 修止损悖论 / 加滑点 / 加冷却期。
    # 1) 去未来函数：开仓决策只看已完成K线i的收盘，成交放到次根K线i+1开盘；止损基准用上一根ATR(atr_ref)。
    # 2) 止损悖论：多空止损价在建仓时按入场价±k*atr_ref锁死(long_stop/short_stop)，使“止损”结构性亏损；
    #    CDP穿越改名为“破CDP离场”(可盈可亏)，不再叫“止损”。
    # 3) 成交价夹逼：止盈(限价)用 max/min(open,目标)；止损(触价)跳空时以开盘成交。
    # 4) 滑点：买入 *(1+slippage)，卖出 *(1-slippage)。
    # 5) 冷却期：离场后 cooldown_bars 根内不再同向开仓，反手不受限。多空互斥。
    df = df.reset_index(drop=True)
    n = len(df); capital = INITIAL_CAPITAL
    position = 0.0; avg_cost = 0.0; long_stop = 0.0; long_entry_idx = -1
    short_position = 0.0; short_entry = 0.0; short_stop = 0.0; short_entry_idx = -1
    pending_long = False; pending_short = False
    last_exit_idx = -10 ** 9; last_exit_dir = None
    trades = []
    idx = 0

    def close_long(price, date, reason):
        nonlocal capital, position, last_exit_idx, last_exit_dir
        shares = position
        commission = max(shares * price * COMMISSION_RATE, min_commission)
        stamp = shares * price * stamp_duty_rate
        pnl = (price - avg_cost) * shares - commission - stamp
        capital += shares * price - commission - stamp
        trades.append({"symbol": symbol, "buy_date": None, "sell_date": str(date),
                       "buy_price": avg_cost, "sell_price": price, "shares": shares,
                       "pnl": pnl, "pnl_pct": (price / avg_cost - 1) if avg_cost > 0 else 0,
                       "reason": reason})
        position = 0.0
        last_exit_idx = idx; last_exit_dir = "long"

    def close_short(price, date, reason):
        nonlocal capital, short_position, last_exit_idx, last_exit_dir
        shares = short_position
        commission = max(shares * price * COMMISSION_RATE, min_commission)
        stamp = shares * price * stamp_duty_rate
        pnl = (short_entry - price) * shares - commission - stamp
        capital += pnl
        trades.append({"symbol": symbol, "buy_date": None, "sell_date": str(date),
                       "buy_price": short_entry, "sell_price": price, "shares": shares,
                       "pnl": pnl, "pnl_pct": (short_entry - price) / short_entry if short_entry > 0 else 0,
                       "reason": reason})
        short_position = 0.0
        last_exit_idx = idx; last_exit_dir = "short"

    for i in range(1, n):
        idx = i
        row = df.iloc[i]
        o = row["open"]; h = row["high"]; l = row["low"]; c = row["close"]
        cdp = row["CDP"]; nh = row["NH"]; nl = row["NL"]; atr = row["atr_ref"]
        if pd.isna(cdp) or pd.isna(nh) or pd.isna(nl) or pd.isna(atr) or atr <= 0:
            continue

        # A) 开盘执行上根收盘信号（反手先平反向仓）
        if pending_long:
            if short_position > 0:
                close_short(o * (1 + slippage), row["date"], "反手平空")
            if position == 0 and short_position == 0:
                bp = o * (1 + slippage)
                sh = int((capital * POSITION_RATIO) / bp / lot_size) * lot_size
                if sh > 0:
                    commission = max(sh * bp * COMMISSION_RATE, min_commission)
                    cost = sh * bp + commission
                    if cost <= capital:
                        capital -= cost
                        position = float(sh); avg_cost = bp
                        long_stop = bp - atr_multiplier * atr; long_entry_idx = i
                        trades.append({"symbol": symbol, "buy_date": str(row["date"]),
                                       "sell_date": None, "buy_price": bp, "sell_price": None,
                                       "shares": sh, "pnl": 0, "pnl_pct": 0, "reason": "买入"})
        if enable_short and pending_short:
            if position > 0:
                close_long(o * (1 - slippage), row["date"], "反手平多")
            if position == 0 and short_position == 0:
                sp = o * (1 - slippage)
                sh = int((capital * POSITION_RATIO) / sp / lot_size) * lot_size
                if sh > 0:
                    commission = max(sh * sp * COMMISSION_RATE, min_commission)
                    capital -= commission
                    short_position = float(sh); short_entry = sp
                    short_stop = sp + atr_multiplier * atr; short_entry_idx = i
                    trades.append({"symbol": symbol, "buy_date": str(row["date"]),
                                   "sell_date": None, "buy_price": sp, "sell_price": None,
                                   "shares": sh, "pnl": 0, "pnl_pct": 0, "reason": "做空"})
        pending_long = False; pending_short = False

        # B) 多头盘中平仓（止损优先 > NH止盈 > 破CDP离场）
        can_sell = position if is_t0 else (position if i > long_entry_idx else 0.0)
        if can_sell > 0:
            if l <= long_stop:
                close_long(min(o, long_stop) * (1 - slippage), row["date"], "ATR止损")
            elif h >= nh:
                close_long(max(o, nh) * (1 - slippage), row["date"], "NH卖")
            elif l < cdp:
                close_long(min(o, cdp) * (1 - slippage), row["date"], "破CDP离场")

        # C) 空头盘中平仓（止损优先 > NL止盈 > 破CDP离场）
        if short_position > 0:
            if h >= short_stop:
                close_short(max(o, short_stop) * (1 + slippage), row["date"], "ATR止损(空)")
            elif l <= nl:
                close_short(min(o, nl) * (1 + slippage), row["date"], "NL止盈")
            elif h > cdp:
                close_short(max(o, cdp) * (1 + slippage), row["date"], "破CDP离场(空)")

        # D) 收盘产生开仓信号（仅空仓时），同向受冷却限
        if position == 0 and short_position == 0:
            blocked = (i - last_exit_idx) < cooldown_bars
            if c > cdp:
                if not (blocked and last_exit_dir == "long"):
                    pending_long = True
            elif c < cdp:
                if not (blocked and last_exit_dir == "short"):
                    pending_short = True

    last = df.iloc[-1]
    if position > 0:
        close_long(last["close"], last["date"], "期末平仓")
    if short_position > 0:
        close_short(last["close"], last["date"], "期末平仓(空)")

    open_reasons = ("买入", "做空")
    sell_trades = [t for t in trades if t["reason"] not in open_reasons]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    total_pnl = capital - INITIAL_CAPITAL
    ret = total_pnl / INITIAL_CAPITAL * 100
    avg_pnl_pct = np.mean([t["pnl_pct"] for t in sell_trades]) * 100 if sell_trades else 0
    win_rate = len(wins) / len(sell_trades) * 100 if sell_trades else 0
    stats = {"symbol": symbol, "rows": n, "trades": len(sell_trades), "win_rate": win_rate,
             "avg_pnl_pct": avg_pnl_pct, "total_pnl": total_pnl, "return_pct": ret}
    reason_list = ["ATR止损", "NH卖", "破CDP离场", "期末平仓",
                   "ATR止损(空)", "NL止盈", "破CDP离场(空)", "反手平多", "反手平空", "期末平仓(空)"]
    reason_stats = []
    for reason in reason_list:
        rt = [t for t in sell_trades if t["reason"] == reason]
        if not rt:
            continue
        rw = [t for t in rt if t["pnl"] > 0]
        reason_stats.append({"reason": reason, "count": len(rt),
                             "win_rate": len(rw) / len(rt) * 100,
                             "avg_pct": np.mean([t["pnl_pct"] for t in rt]) * 100})
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


# ==================== OKXLiveTrader 模拟盘下单器 ====================

class OKXLiveTrader:
    """连接OKX模拟盘/实盘，把回测信号映射成市价单。
    }p`AAAAAAAAAAAA QQQQQQQQQQQQAQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ                                                                                                                                                                                              








"+)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           "            import ccxt
            self.exchange = ccxt.okx({
                "apiKey": OKX_API_KEY,
                "secret": OKX_API_SECRET,
                "password": OKX_API_PASSPHRASE,
                "enableRateLimit": True,
            })
            if OKX_DEMO_MODE:
                self.exchange.set_sandbox_mode(True)
                print("[OKXLiveTrader] 已切换至 Demo Trading 模拟盘")
            if OKX_USE_PROXY:
                self.exchange.proxies = {
                    "http": f"http://127.0.0.1:{OKX_PROXY_PORT}",
                    "https": f"http://127.0.0.1:{OKX_PROXY_PORT}",
                }
            self.market = TRADE_MARKET
            self.enabled = True
            # 测试连接
            self.exchange.fetch_markets()
            print("[OKXLiveTrader] 连接OKX成功，已启用")
        except Exception as e:
            print(f"[OKXLiveTrader] 初始化失败: {e}，已禁用实单")
            self.enabled = False

    def _normalize_symbol(self, symbol):
        """BTC/USDT -> BTCUSDT"""
        return symbol.replace("/", "")

    def _get_contract_size(self, symbol):
        """获取合约面值（swap下每张合约代表的币数量）"""
        try:
            markets = self.exchange.fetch_markets()
            okx_sym = self._normalize_symbol(symbol)
            for m in markets:
                if m["id"] == okx_sym and m.get("type") == "swap":
                    contract_size = m.get("contractSize")
                    if contract_size:
                        return float(contract_size)
            return 1.0
        except Exception:
            return 1.0

    def send_order(self, symbol, action, qty, reason, current_idx, total_bars):
        """发送订单到OKX
        
        action: "open_long"=开多, "close_long"=平多, "open_short"=开空, "close_short"=平空
        qty: 数量（币的数量，不是张数）
        """
        if not self.enabled:
            return

        # 只处理最近N根K线的信号
        if total_bars - current_idx > LIVE_TRADE_RECENT_BARS:
            return

        okx_symbol = self._normalize_symbol(symbol)

        try:
            if self.market == "spot":
                # 现货只能做多，买入=开多，卖出=平多
                if action in ("open_long", "close_long"):
                    if action == "open_long":
                        self.exchange.create_market_buy_order(okx_symbol, qty)
                        print(f"[OKX实单] {reason} | 买入(开多) {qty} {okx_symbol}")
                    else:
                        self.exchange.create_market_sell_order(okx_symbol, qty)
                        print(f"[OKX实单] {reason} | 卖出(平多) {qty} {okx_symbol}")
                else:
                    print(f"[OKX实单] 现货不支持{action}信号，跳过")
            elif self.market == "swap":
                # 永续合约
                contract_size = self._get_contract_size(symbol)
                # 将币的数量转换为张数
                if contract_size and contract_size > 1:
                    ordersize = qty / contract_size
                else:
                    ordersize = qty

                if action == "open_long":
                    # 开多
                    self.exchange.create_market_order(
                        okx_symbol, "buy",
                        {"posSide": "long", "sz": str(ordersize)}
                    )
                    print(f"[OKX实单] {reason} | 开多 {qty} {okx_symbol} ({ordersize}张)")
                elif action == "close_long":
                    # 平多（减仓）
                    self.exchange.create_market_order(
                        okx_symbol, "sell",
                        {"posSide": "long", "sz": str(ordersize), "reduceOnly": "true"}
                    )
                    print(f"[OKX实单] {reason} | 平多 {qty} {okx_symbol} ({ordersize}张)")
                elif action == "open_short":
                    # 开空
                    self.exchange.create_market_order(
                        okx_symbol, "sell",
                        {"posSide": "short", "sz": str(ordersize)}
                    )
                    print(f"[OKX实单] {reason} | 开空 {qty} {okx_symbol} ({ordersize}张)")
                elif action == "close_short":
                    # 平空（减仓）
                    self.exchange.create_market_order(
                        okx_symbol, "buy",
                        {"posSide": "short", "sz": str(ordersize), "reduceOnly": "true"}
                    )
                    print(f"[OKX实单] {reason} | 平空 {qty} {okx_symbol} ({ordersize}张)")
                else:
                    print(f"[OKX实单] 未知action: {action}，跳过")
        except ccxt.InsufficientFunds as e:
            print(f"[OKX实单] 资金不足 {reason}: {e}")
        except ccxt.InvalidOrder as e:
            print(f"[OKX实单] 订单无效 {reason}: {e}")
        except Exception as e:
            print(f"[OKX实单] 下单失败 {reason}: {e}")


# ==================== OKX 数据源 ====================


def fetch_okx_klines(symbol, timeframe="1d", limit=1000, use_proxy=False, proxy_port=7890):
    if not _HAS_CCXT:
        print("未安装 ccxt，请先 pip install ccxt")
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
            print(f"  拉取失败: {e}")
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
        print("  未拉到任何数据")
        return None

    all_klines.sort(key=lambda x: x[0])
    all_klines = all_klines[-limit:]
    df = pd.DataFrame(all_klines, columns=["ts", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.strftime("%Y-%m-%d")
    df = df[["date", "open", "high", "low", "close", "volume"]]
    print(f"拉取成功！共 {len(df)} 条数据。")
    return df

# ==================== OKX 多币种回测入口 ====================

def run_okx_multi():
    if not _HAS_CCXT:
        print("未安装 ccxt，请先 pip install ccxt")
        return
    # 初始化模拟盘交易器
    trader = OKXLiveTrader()
    all_stats = []
    all_reason_stats = []
    for sym in OKX_SYMBOLS:
        print(f"\n{'='*80}")
        df = fetch_okx_klines(sym, timeframe=OKX_TIMEFRAME, limit=OKX_LIMIT,
                               use_proxy=OKX_USE_PROXY, proxy_port=OKX_PROXY_PORT)
        if df is None or len(df) < 60:
            print(f"  {sym} 数据不足，跳过\n")
            continue
        print(df.head().to_string(index=False))
        print("...")
        print(df.tail().to_string(index=False))

        df = compute_cdp_atr(df)
        _, trades, stats, reason_stats = run_backtest_on_df(
            df, symbol=sym, lot_size=LOT_SIZE, stamp_duty_rate=STAMP_DUTY_RATE,
            min_commission=MIN_COMMISSION,
            atr_multiplier=ATR_MULTIPLIER,
            is_t0=True, skip_holiday_gap=False,
            enable_short=True,
            slippage=SLIPPAGE, cooldown_bars=COOLDOWN_BARS,
        )
        if stats:
            all_stats.append(stats)
            all_reason_stats.extend(reason_stats)

        # 回测结束后，对最近N根K线的交易信号发送实盘订单
        if trader.enabled and trades:
            print(f"\n[OKX实单] 开始回放 {sym} 最近交易信号...")
            total_bars = len(df)
            for t in trades:
                # 确定当前交易的bar索引（通过日期匹配）
                trade_date = t.get("buy_date") or t.get("sell_date")
                if trade_date is None:
                    continue
                bar_idx = None
                for j in range(len(df)):
                    if str(df.iloc[j]["date"]) == str(trade_date):
                        bar_idx = j
                        break
                if bar_idx is None:
                    continue

                # 根据reason判断下单方向
                reason = t.get("reason", "")
                qty = abs(t.get("shares", 0))
                if qty <= 0:
                    continue

                if reason in ("买入",):
                    # 开多
                    trader.send_order(sym, "open_long", qty, reason, bar_idx, total_bars)
                elif reason in ("做空",):
                    # 开空
                    trader.send_order(sym, "open_short", qty, reason, bar_idx, total_bars)
                elif reason in ("ATR止损", "NH卖", "破CDP离场", "期末平仓",
                                "反手平多"):
                    # 平多（卖出）
                    trader.send_order(sym, "close_long", qty, reason, bar_idx, total_bars)
                elif reason in ("ATR止损(空)", "NL止盈", "破CDP离场(空)",
                                "期末平仓(空)", "反手平空"):
                    # 平空（买入）
                    trader.send_order(sym, "close_short", qty, reason, bar_idx, total_bars)
                else:
                    print(f"[OKX实单] 未知原因跳过: {reason}")

    print()
    print("=" * 80)
    print(f"OKX 加密货币 CDP/ATR 回测（T+0 / 无印花税 / ATR倍数={ATR_MULTIPLIER} / 最小单位={LOT_SIZE}）")
    print(f"本金：{INITIAL_CAPITAL:,} USDT")
    print("=" * 80)
    print()
    if all_stats:
        print_summary(all_stats, all_reason_stats)
    else:
        print("没有可回测的币种数据")

# ==================== 主程序 ====================

if __name__ == "__main__":
    run_okx_multi()
