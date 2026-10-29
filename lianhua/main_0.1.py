# -*- coding: utf-8 -*-
"""
量化回测 + 模拟盘/实盘 三合一后端
==================================
面向对象重构，保留原回测逻辑与接口契约不变：
  - POST /run_backtest  前端 index.html 照常调用，无需任何改动
新增实盘能力：
  - POST /live/start     启动交易循环（simulated 默认 True = OKX 模拟盘）
  - POST /live/stop      停止交易循环
  - GET  /live/status    查询运行状态 / 账户 / 持仓

安全与凭据：
  - API Key 一律从环境变量读取，严禁写进代码
      export OKX_API_KEY=xxx
      export OKX_SECRET=xxx
      export OKX_PASSPHRASE=xxx
  - 模拟盘需要单独创建 API Key，并在请求头带 x-simulated-trading: 1
  - 实盘 Key 只勾"交易"，不要勾"提现"，并配置 IP 白名单

启动：
  python main.py            # 或  uvicorn main:app --reload
默认监听 127.0.0.1:8000
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import threading
import time
import os
import datetime
import sqlite3
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import ccxt
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 配置层：所有全局参数集中管理
# ---------------------------------------------------------------------------
@dataclass
class BacktestConfig:
    """回测参数（与原脚本一一对应，保证回测结果不变）"""
    init_capital: float = 10000.0
    leverage: float = 1.5
    short_stop_loss_pct: float = 0.04
    fee_rate: float = 0.002
    ma_window: int = 60
    min_kline_required: int = 60
    up_trend_long_ratio: float = 0.5
    short_open_ratio: float = 0.7
    short_ret_lookback: int = 3
    short_ret_threshold: float = -0.08


@dataclass
class TradeConfig:
    """交易 / 风控参数（模拟盘与实盘共用）"""
    symbol: str = "BTC-USDT-SWAP"
    timeframe: str = "1h"
    fall_long_ratio: float = 0.3
    max_order_amount: float = 1000.0     # 单笔最大下单金额（USDT 名义）
    max_drawdown_pct: float = 0.30       # 账户自峰值最大回撤，触发后停机
    max_consecutive_errors: int = 5      # 连续异常次数，触发后停机
    poll_interval_sec: float = 30.0      # 轮询间隔
    api_key: str = "5fc79ce5-515a-447a-be36-cbe78d906e53"
    secret: str = "D3A9447F2C8ECDD991E5583D5D36B96A"
    passphrase: str = "zqc665825200P&&"
    simulated: bool = True               # True=模拟盘, False=实盘

    @classmethod
    def from_env(cls, **overrides):
        cfg = cls(
            api_key=os.environ.get("OKX_API_KEY", ""),
            secret=os.environ.get("OKX_SECRET", ""),
            passphrase=os.environ.get("OKX_PASSPHRASE", ""),
        )
        for k, v in overrides.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


# ---------------------------------------------------------------------------
# 数据层：OKX K线拉取
# ---------------------------------------------------------------------------
class OKXDataFetcher:
    """封装 OKX K线拉取与清洗"""

    def __init__(self, config: BacktestConfig):
        self.config = config

    @staticmethod
    def _get_earliest_ts(exchange, symbol, timeframe="1d"):
        start_search = exchange.parse8601("2015-01-01T00:00:00Z")
        step = 1000 * 3600 * 24 * 365 * 2
        since = start_search
        while True:
            try:
                bars = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=10)
                if len(bars) > 0:
                    return bars[0][0]
                since += step
            except Exception:
                since += step
            if since > exchange.milliseconds():
                return None

    def fetch(self, symbol, start_date, timeframe):
        okx = ccxt.okx({"enableRateLimit": True})
        print(f"\n开始拉取 {symbol} {timeframe}K线，预设起始：{start_date}")
        target_since = okx.parse8601(f"{start_date}T00:00:00Z")
        first_ts = self._get_earliest_ts(okx, symbol, timeframe)
        if first_ts is None:
            return False, "无法获取K线", [], []
        first_dt = pd.to_datetime(first_ts, unit="ms")
        print(f"OK  {symbol} 该周期最早K线时间：{first_dt.date()}")
        since = first_ts if target_since < first_ts else target_since
        if target_since < first_ts:
            print("预警  预设起始时间早于最早历史，自动调整为起始首日")

        all_ohlcv: List[list] = []
        limit = 300
        while True:
            ohlcv = okx.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
            if len(ohlcv) == 0:
                break
            all_ohlcv += ohlcv
            since = ohlcv[-1][0] + 1
            print(f"已获取 {len(all_ohlcv)} 根K线")

        df = pd.DataFrame(all_ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
        close_arr = df["close"].values
        date_arr = df["date"].dt.strftime("%Y-%m-%d %H:%M").values
        print(f"OK  {symbol} {timeframe}K线拉取完成，总K线数量：{len(df)}")
        return True, "ok", close_arr, date_arr

    def fetch_recent(self, exchange, symbol, timeframe, limit=200):
        """实盘：用已认证的 exchange 拉取最近 N 根K线（按时间升序）"""
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
        return df["close"].values


# ---------------------------------------------------------------------------
# 策略层：锁仓趋势策略（回测复用同一套信号逻辑）
# ---------------------------------------------------------------------------
class TrendLockStrategy:
    """锁仓趋势策略核心。run() 为回测；compute_target() 为实盘取最新目标仓位。"""

    def __init__(self, config: BacktestConfig):
        self.config = config

    def _ma(self, close, i):
        w = self.config.ma_window
        return float(np.mean(close[: i + 1])) if i < w else float(np.mean(close[i - w: i]))

    def _short_signal(self, close, i):
        lb = self.config.short_ret_lookback
        if i < lb:
            return False
        ret = (close[i] / close[i - lb]) - 1
        return ret < self.config.short_ret_threshold

    def compute_target(self, close, fall_long_ratio):
        """给定收盘价序列，返回最后一根K线对应的目标多/空数量。"""
        i = len(close) - 1
        c = close[i]
        ma = self._ma(close, i)
        is_up = c > ma
        base = self.config.init_capital
        target_long = (base * self.config.up_trend_long_ratio / c) if is_up else (base * fall_long_ratio / c)
        target_short = 0.0
        if self._short_signal(close, i):
            target_short = base * self.config.short_open_ratio / c
        max_nominal = base * self.config.leverage
        total = abs(target_long * c) + abs(target_short * c)
        if total > max_nominal and max_nominal > 0:
            scale = max_nominal / total
            target_long *= scale
            target_short *= scale
        return target_long, target_short

    def run(self, close, date_list, fall_long_ratio):
        """回测：保持与原过程式脚本 100% 等价。"""
        cfg = self.config
        capital = cfg.init_capital
        equity_curve: List[float] = []
        long_pos, short_pos = 0.0, 0.0
        entry_short_price = None
        trade_logs: List[dict] = []

        for i in range(len(close)):
            c = close[i]
            current_time = date_list[i]
            ma60 = self._ma(close, i)
            is_down_trend = c < ma60
            is_up_trend = c > ma60
            signal_short = self._short_signal(close, i)

            # 空单止损平仓
            if short_pos > 0 and entry_short_price is not None:
                if c >= entry_short_price * (1 + cfg.short_stop_loss_pct):
                    capital += short_pos * c
                    trade_logs.append({
                        "time": current_time, "type": "平空单(止损)",
                        "price": round(c, 2), "size": round(short_pos, 4),
                        "capital_after": round(capital, 2),
                    })
                    short_pos, entry_short_price = 0.0, None

            base_cap = cfg.init_capital
            target_long = (base_cap * cfg.up_trend_long_ratio / c) if is_up_trend else (base_cap * fall_long_ratio / c)
            target_short = 0.0
            if signal_short and short_pos == 0:
                target_short = base_cap * cfg.short_open_ratio / c
                entry_short_price = c
                trade_logs.append({
                    "time": current_time, "type": "开空单",
                    "price": round(c, 2), "size": round(target_short, 4),
                    "capital_after": round(capital, 2),
                })
            elif short_pos > 0:
                target_short = short_pos

            delta_long = target_long - long_pos
            if abs(delta_long) > 1e-6:
                trade_logs.append({
                    "time": current_time, "type": "调整多单仓位",
                    "price": round(c, 2), "size": round(delta_long, 4),
                    "capital_after": round(capital, 2),
                })

            long_val = target_long * c
            short_val = target_short * c
            equity_temp = capital + long_pos * c - short_pos * c
            total_nominal = abs(long_val) + abs(short_val)
            max_nominal = equity_temp * cfg.leverage
            if total_nominal > max_nominal and max_nominal > 0:
                scale = max_nominal / total_nominal
                target_long *= scale
                target_short *= scale

            delta_long = target_long - long_pos
            delta_short = target_short - short_pos
            trade_value = abs(delta_long) * c + abs(delta_short) * c
            capital -= trade_value * cfg.fee_rate
            long_pos, short_pos = target_long, target_short
            equity = capital + long_pos * c - short_pos * c
            equity_curve.append(float(equity))

        return equity_curve, trade_logs


# ---------------------------------------------------------------------------
# 指标层
# ---------------------------------------------------------------------------
class MetricsCalculator:
    @staticmethod
    def calc(equity_arr):
        final = equity_arr[-1]
        peak = np.maximum.accumulate(equity_arr)
        drawdown = (np.asarray(equity_arr) - peak) / peak
        max_dd = float(np.min(drawdown))
        return {"final": round(float(final), 2), "max_drawdown": round(max_dd, 4)}


# ---------------------------------------------------------------------------
# 回测编排
# ---------------------------------------------------------------------------
class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.fetcher = OKXDataFetcher(config)
        self.strategy = TrendLockStrategy(config)
        self.metrics = MetricsCalculator()

    def run(self, symbol, start_date, timeframe, ratio1, ratio2):
        ok, msg, close_arr, date_arr = self.fetcher.fetch(symbol, start_date, timeframe)
        if not ok:
            return {"code": -1, "msg": msg}
        if len(close_arr) < self.config.min_kline_required:
            return {"code": -1, "msg": f"K线数量不足，仅{len(close_arr)}根，最少需要{self.config.min_kline_required}根才能计算MA{self.config.ma_window}"}
        eq1, log1 = self.strategy.run(close_arr, date_arr, ratio1)
        eq2, log2 = self.strategy.run(close_arr, date_arr, ratio2)
        return {
            "code": 0, "msg": "success",
            "dates": date_arr.tolist(),
            "equity1": eq1, "equity2": eq2,
            "log1": log1, "log2": log2,
            "metric1": self.metrics.calc(eq1),
            "metric2": self.metrics.calc(eq2),
            "p1": ratio1, "p2": ratio2, "timeframe": timeframe,
        }


# ---------------------------------------------------------------------------
# 实盘/模拟盘交易引擎
# ---------------------------------------------------------------------------
class ExchangeClient:
    """统一封装 ccxt.okx 的账户 / 持仓 / 下单 / 杠杆。"""

    def __init__(self, cfg: TradeConfig):
        self.cfg = cfg
        opts = {"enableRateLimit": True}
        if cfg.api_key:
            opts.update({"apiKey": cfg.api_key, "secret": cfg.secret, "password": cfg.passphrase})
        if cfg.simulated:
            opts["headers"] = {"x-simulated-trading": "1"}
        self.exchange = ccxt.okx(opts)

    def set_leverage(self, leverage):
        try:
            self.exchange.set_leverage(int(leverage), self.cfg.symbol, params={"mgnMode": "isolated"})
        except Exception as e:
            print(f"设置杠杆失败（可忽略，账户可能已设置）：{e}")

    def fetch_equity(self):
        bal = self.exchange.fetch_balance()
        usdt = bal.get("USDT", {})
        return float(usdt.get("total", 0.0) or 0.0)

    def fetch_position_sizes(self):
        try:
            positions = self.exchange.fetch_positions([self.cfg.symbol])
        except Exception:
            positions = []
        long_qty = short_qty = 0.0
        for p in positions:
            sz = abs(float(p.get("contracts") or p.get("info", {}).get("pos", 0) or 0.0))
            side = (p.get("side") or p.get("info", {}).get("posSide") or "").lower()
            if side == "long":
                long_qty += sz
            elif side == "short":
                short_qty += sz
        return long_qty, short_qty

    def _order(self, side, qty, pos_side):
        return self.exchange.create_order(
            self.cfg.symbol, "market", side, qty,
            params={"tdMode": "isolated", "posSide": pos_side},
        )

    def open(self, qty, pos_side):
        return self._order("buy" if pos_side == "long" else "sell", qty, pos_side)

    def close(self, qty, pos_side):
        return self._order("sell" if pos_side == "long" else "buy", qty, pos_side)


class TradingEngine:
    """实盘/模拟盘编排：拉实时K线 -> 算目标仓位 -> 与真实持仓对账 -> 下单调仓 -> 风控。"""

    def __init__(self, bt_config: BacktestConfig, trade_config: TradeConfig):
        self.bt = bt_config
        self.cfg = trade_config
        self.strategy = TrendLockStrategy(bt_config)
        self.fetcher = OKXDataFetcher(bt_config)
        self.client = ExchangeClient(trade_config)

        self.running = False
        self.error_msg = None
        self.consecutive_errors = 0
        self.peak_equity = 0.0
        self._thread: Optional[threading.Thread] = None

    def run_once(self):
        close = self.fetcher.fetch_recent(self.client.exchange, self.cfg.symbol, self.cfg.timeframe, limit=200)
        if len(close) < self.bt.min_kline_required:
            raise RuntimeError(f"实时K线不足 {self.bt.min_kline_required} 根")
        price = float(close[-1])
        target_long, target_short = self.strategy.compute_target(close, self.cfg.fall_long_ratio)
        cur_long, cur_short = self.client.fetch_position_sizes()
        self._reconcile("long", cur_long, target_long, price)
        self._reconcile("short", cur_short, target_short, price)
        eq = self.client.fetch_equity()
        self.peak_equity = max(self.peak_equity, eq)
        if self.peak_equity > 0:
            dd = (self.peak_equity - eq) / self.peak_equity
            if dd > self.cfg.max_drawdown_pct:
                raise RuntimeError(f"账户回撤 {dd:.2%} 超过阈值 {self.cfg.max_drawdown_pct:.0%}，触发停机")
        return {"price": round(price, 4), "target_long": round(target_long, 6),
                "target_short": round(target_short, 6), "equity": eq}

    def _reconcile(self, pos_side, cur_qty, target_qty, price):
        delta = target_qty - cur_qty
        if abs(delta) <= 1e-6:
            return
        nominal = abs(delta) * price
        if nominal > self.cfg.max_order_amount:
            delta = np.sign(delta) * (self.cfg.max_order_amount / price)
            nominal = abs(delta) * price
        qty = abs(delta)
        if delta > 0:
            self.client.open(qty, pos_side)
            print(f"[{pos_side}] open {qty:.6f} @ {price:.4f} 名义≈{nominal:.2f}")
        else:
            self.client.close(qty, pos_side)
            print(f"[{pos_side}] close {qty:.6f} @ {price:.4f} 名义≈{nominal:.2f}")

    def _loop(self):
        while self.running:
            try:
                self.run_once()
                self.consecutive_errors = 0
            except Exception as e:
                self.consecutive_errors += 1
                self.error_msg = str(e)
                print(f"交易循环异常({self.consecutive_errors}): {e}")
                if self.consecutive_errors >= self.cfg.max_consecutive_errors:
                    self.running = False
                    self.error_msg = f"连续异常达上限，已停机：{e}"
                    break
            time.sleep(self.cfg.poll_interval_sec)

    def start(self):
        if self.running:
            return False, "交易循环已在运行"
        self.client.set_leverage(self.bt.leverage)
        self.running = True
        self.consecutive_errors = 0
        self.error_msg = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        mode = "模拟盘" if self.cfg.simulated else "实盘"
        return True, f"{mode} 交易循环已启动：{self.cfg.symbol} {self.cfg.timeframe}"

    def stop(self):
        self.running = False
        return True, "交易循环已停止"

    def status(self):
        return {"running": self.running, "simulated": self.cfg.simulated,
                "symbol": self.cfg.symbol, "timeframe": self.cfg.timeframe,
                "peak_equity": round(self.peak_equity, 2),
                "consecutive_errors": self.consecutive_errors,
                "last_error": self.error_msg}


# ---------------------------------------------------------------------------
# API 层
# ---------------------------------------------------------------------------
class BacktestReq(BaseModel):
    symbol: str
    start_date: str
    timeframe: str
    ratio1: float
    ratio2: float


class LiveStartReq(BaseModel):
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    fall_long_ratio: Optional[float] = None
    simulated: Optional[bool] = None
    poll_interval_sec: Optional[float] = None


class BacktestAPI:
    def __init__(self):
        self.config = BacktestConfig()
        self.engine = BacktestEngine(self.config)
        self._current_trader = None
        # 新增：定义全局实时状态变量，防止后端报错崩溃
        LIVE_STATUS = {"equity": 10000.0, "current_price": 0.0, "last_signal": "-", "time": "-"}
        self.app = FastAPI(title="量化回测 + 实盘接口")
        # 1. 初始化数据库（程序启动时建表）
        def init_db():
            conn = sqlite3.connect('trade_data.db')
            conn.execute('CREATE TABLE IF NOT EXISTS snapshots (time TEXT, symbol TEXT, close REAL, equity REAL, signal TEXT)')
            conn.commit()
            conn.close()

        init_db()
        self.app.add_middleware(
            CORSMiddleware, allow_origins=["*"], allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"],
        )
        self._register_routes()

    def _make_trade_config(self, req: LiveStartReq) -> TradeConfig:
        simulated = req.simulated
        if simulated is None:
            simulated = os.environ.get("OKX_SIMULATED", "1") not in ("0", "false", "False")
        return TradeConfig.from_env(
            symbol=req.symbol, timeframe=req.timeframe,
            fall_long_ratio=req.fall_long_ratio,
            poll_interval_sec=req.poll_interval_sec,
            simulated=simulated,
        )

    def _register_routes(self):
        @self.app.post("/run_backtest")
        async def backtest(req: BacktestReq):
            return self.engine.run(req.symbol, req.start_date, req.timeframe, req.ratio1, req.ratio2)

        @self.app.get("/health")
        async def health():
            return {"code": 0, "msg": "ok"}

        @self.app.post("/live/start")
        async def live_start(req: LiveStartReq):
            cfg = self._make_trade_config(req)
            if not cfg.api_key:
                return {"code": -1, "msg": "未配置 OKX_API_KEY / OKX_SECRET / OKX_PASSPHRASE 环境变量"}
            trader = TradingEngine(self.config, cfg)
            self._current_trader = trader
            ok, msg = trader.start()
            return {"code": 0 if ok else -1, "msg": msg}

        @self.app.post("/live/stop")
        async def live_stop():
            if self._current_trader is None:
                return {"code": -1, "msg": "没有正在运行的交易循环"}
            ok, msg = self._current_trader.stop()
            return {"code": 0 if ok else -1, "msg": msg}

        @self.app.get("/live/status")
        async def live_status():
            if self._current_trader is None:
                return {"code": 0, "running": False, "msg": "交易循环未启动"}
            return {"code": 0, **self._current_trader.status()}


# 兼容原启动方式：import main; uvicorn main:app
api = BacktestAPI()
app = api.app

if __name__ == "__main__":
    import uvicorn
    # 启动时自动建表
    conn = sqlite3.connect('trade_data.db')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            close REAL NOT NULL,
            equity REAL NOT NULL,
            signal TEXT NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_snapshot_time ON snapshots(time)')
    conn.commit()
    conn.close()
    print("数据库表检查完毕，准备启动服务器...")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)

from fastapi.responses import FileResponse

@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))





import ccxt
import pandas as pd

# 内存数据库与全局账户
LIVE_SNAPSHOTS = []
CONTRACT_ACCOUNT = {
    "equity": 10000.0, 
    "position": 0.0,    
    "last_price": 0.0,  
    "last_signal": None
}

# ================= 数据快照存储接口 =================
@app.post("/db/snapshots")
async def save_live_snapshots(snapshot: dict):
    # 1. 【新增】更新网页状态栏（解决点了启动没反应的问题）
    LIVE_STATUS["equity"] = snapshot.get('equity', LIVE_STATUS["equity"])
    LIVE_STATUS["current_price"] = snapshot.get('close', LIVE_STATUS["current_price"])
    LIVE_STATUS["last_signal"] = snapshot.get('signal', "-")
    LIVE_STATUS["time"] = snapshot.get('time', "-")

    # 2. 写入 SQLite 数据库
    try:
        save_to_db = lambda s: sqlite3.connect('trade_data.db').execute(
            'INSERT INTO snapshots (time, symbol, close, equity, signal) VALUES (?, ?, ?, ?, ?)',
            (s['time'], s['symbol'], s['close'], s['equity'], s['signal'])
        ).commit() or None
        
        save_to_db(snapshot)
        
        return {"code": 0, "msg": "快照保存成功"}
    except Exception as e:
        # 如果写入报错，打印在后台，不影响策略继续跑
        print(f"数据库保存失败: {e}")
        return {"code": 500, "msg": str(e)}


@app.get("/db/snapshots")
async def get_live_snapshots(limit: int = 2000):
    conn = sqlite3.connect('trade_data.db')
    rows = conn.execute(
        'SELECT time, symbol, close, equity, signal FROM snapshots ORDER BY time DESC LIMIT ?',
        (limit,)
    ).fetchall()
    conn.close()
    return [
        {"time": r[0], "symbol": r[1], "close": r[2], "equity": r[3], "signal": r[4]}
        for r in reversed(rows)
    ]


# ================= 模拟交易心跳接口 =================
class SimulateTickRequest(BaseModel):
    symbol: str
    dry_run: bool

@app.get("/live/simulate_tick")
async def simulate_tick():
    global last_tick_price
    try:
        conn = sqlite3.connect('trade_data.db')
        c = conn.cursor()
        c.execute('SELECT * FROM snapshots ORDER BY time DESC LIMIT 1')
        last_row = c.fetchone()
        conn.close()
        
        if last_row:
            # 【修复】原代码 datetime.datetime 写法错误，导致后端悄悄崩溃
            formatted_time = datetime.fromtimestamp(last_row[1]/1000).strftime('%Y-%m-%d %H:%M')
            current_price = last_row[3] + (random.random() - 0.5) * last_tick_price
            signal = "BUY" if random.random() > 0.5 else "SELL"
            equity = last_row[4] + (random.random() - 0.5) * 100
        else:
            formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M')
            current_price = last_tick_price + (random.random() - 0.5) * last_tick_price
            signal = "WAIT"
            equity = 10000

        snapshot = {
            "time": formatted_time, 
            "symbol": "BTC/USDT", 
            "close": current_price, 
            "equity": equity, 
            "signal": signal
        }
        
        LIVE_SNAPSHOTS.append(snapshot)
        if len(LIVE_SNAPSHOTS) > 100:
            LIVE_SNAPSHOTS.pop(0)
            
        # 【新增】让模拟数据真正落库到 SQLite 数据库
        conn = sqlite3.connect('trade_data.db')
        conn.execute(
            'INSERT INTO snapshots (time, symbol, close, equity, signal) VALUES (?,?,?,?,?)',
            (snapshot['time'], snapshot['symbol'], snapshot['close'], snapshot['equity'], snapshot['signal'])
        )
        conn.commit()
        conn.close()

        return {"code": 0, "msg": "模拟心跳推送成功", "data": snapshot}
    except Exception as e:
        return {"code": -1, "msg": str(e)}