import os
import sqlite3
import threading
import time
import traceback
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ---------- 配置 ----------
DB_PATH = os.path.join(os.path.dirname(__file__), "lianhua.db")

OKX_API_KEY = "你的模拟盘API_KEY"
OKX_SECRET_KEY = "你的模拟盘SECRET"
OKX_PASSPHRASE = "你的模拟盘PASSPHRASE"

# ---------- 数据库 ----------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            symbol TEXT,
            side TEXT,
            price REAL,
            size REAL,
            pnl REAL,
            equity REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            symbol TEXT,
            close REAL,
            equity REAL,
            signal TEXT
        )
        """
    )
    conn.commit()
    conn.close()

def save_trade(time_str, symbol, side, price, size, pnl, equity):
    conn = get_db()
    conn.execute(
        "INSERT INTO trades (time, symbol, side, price, size, pnl, equity) VALUES (?,?,?,?,?,?,?)",
        (time_str, symbol, side, price, size, pnl, equity),
    )
    conn.commit()
    conn.close()

def save_snapshot(time_str, symbol, close, equity, signal):
    conn = get_db()
    conn.execute(
        "INSERT INTO snapshots (time, symbol, close, equity, signal) VALUES (?,?,?,?,?)",
        (time_str, symbol, close, equity, signal),
    )
    conn.commit()
    conn.close()

# ---------- 策略 ----------
class DualThrust:
    def __init__(self, lookback=5, k1=0.7, k2=0.7, long_weight=0.6, short_weight=0.4):
        self.lookback = lookback
        self.k1 = k1
        self.k2 = k2
        self.long_weight = long_weight
        self.short_weight = short_weight

    def run(self, df: pd.DataFrame) -> dict:
        df = df.copy()
        if "time" in df.columns:
            df = df.set_index("time")

        high = df["high"]
        low = df["low"]
        close = df["close"]

        hh = high.rolling(self.lookback).max()
        ll = low.rolling(self.lookback).min()
        prev_close = close.shift(1)

        upper = prev_close + self.k1 * (hh - ll)
        lower = prev_close - self.k2 * (hh - ll)

        position = np.where(close > upper, 1, np.where(close < lower, -1, 0))

        equity = 100000.0
        equity_curve = []

        for i in range(len(df)):
            if position[i] == 1:
                equity *= 1 + (close.iloc[i] / max(close.iloc[i - 1], 1e-9) - 1) * self.long_weight
            elif position[i] == -1:
                equity *= 1 + (close.iloc[i] / max(close.iloc[i - 1], 1e-9) - 1) * self.short_weight

            equity_curve.append(equity)

        return {
            "equity_curve": equity_curve,
            "final_equity": equity,
            "positions": position.tolist(),
            "close": close.values.tolist(),
        }


strategy = DualThrust()

# ---------- 回测 ----------
def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
    return df

def run_backtest(df: pd.DataFrame) -> dict:
    result = strategy.run(df)
    equity_curve = np.array(result["equity_curve"])
    returns = pd.Series(equity_curve).pct_change().dropna()

    total_return = equity_curve[-1] / equity_curve[0] - 1
    sharpe = float(returns.mean() / (returns.std() + 1e-9)) * (252 ** 0.5)
    cum = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - cum) / (cum + 1e-9)
    max_drawdown = float(drawdown.min())

    return {
        "total_return": float(total_return),
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "final_equity": float(result["final_equity"]),
        "equity_curve": equity_curve.tolist(),
    }


class BacktestRequest(BaseModel):
    csv_path: Optional[str] = None
    symbol: Optional[str] = "BTC/USDT:USDT"
    start_date: Optional[str] = "2024-01-01"
    end_date: Optional[str] = "2025-12-31"
    timeframe: Optional[str] = "1d"
    ratio1: Optional[float] = 0.6
    ratio2: Optional[float] = 0.4


# ---------- 模拟盘 ----------
try:
    import ccxt  # noqa
except Exception:
    ccxt = None  # noqa

_okx = None
_live_running = False
_live_thread: Optional[threading.Thread] = None


def get_okx():
    global _okx
    if _okx is None:
        if ccxt is None:
            raise RuntimeError("未安装 ccxt，请先执行 pip install ccxt")
        _okx = ccxt.okx({
            "apiKey": OKX_API_KEY,
            "secret": OKX_SECRET_KEY,
            "password": OKX_PASSPHRASE,
            "enableRateLimit": True,
            "headers": {"x-simulated-trading": "1"},
        })
    return _okx


def fetch_ohlcv_df(ex, symbol: str, timeframe: str = "1d", limit: int = 200) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df[["time", "open", "high", "low", "close", "volume"]]
    return df


class LiveStartRequest(BaseModel):
    symbol: str = "BTC/USDT:USDT"
    interval: int = 60
    dry_run: bool = True


def _live_loop(symbol: str, interval: int, dry_run: bool):
    global _live_running
    try:
        ex = get_okx()
    except Exception as e:
        print("[live] 初始化交易所失败:", e)
        _live_running = False
        return

    print(f"[live] 模拟盘已启动 symbol={symbol} interval={interval}s dry_run={dry_run}")

    while _live_running:
        try:
            ticker = ex.fetch_ticker(symbol)
            close = float(ticker["last"])
            now = datetime.now()

            df = fetch_ohlcv_df(ex, symbol, timeframe="1d", limit=200)
            result = strategy.run(df)

            equity = float(result["final_equity"])
            positions = result.get("positions", [])
            current_signal = positions[-1] if positions else 0

            signal_text = {1: "long", -1: "short", 0: "flat"}.get(current_signal, "flat")

            save_snapshot(now.isoformat(), symbol, close, equity, signal_text)

            print(
                f"[live] {now.isoformat()} {symbol} close={close} equity={equity:.2f} signal={signal_text}"
            )

            if dry_run:
                if current_signal == 1:
                    print(f"[dry_run] 计划开多 {symbol} price={close}")
                elif current_signal == -1:
                    print(f"[dry_run] 计划开空 {symbol} price={close}")
                elif current_signal == 0:
                    print(f"[dry_run] 计划平仓 {symbol} price={close}")
            else:
                # 后续可以接真实下单
                pass

        except Exception as e:
            print("[live error]", e)
            traceback.print_exc()

        time.sleep(interval)

    print("[live] 模拟盘已停止")


# ---------- FastAPI ----------
app = FastAPI()

init_db()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run_backtest")
def run_backtest_endpoint(req: BacktestRequest = BacktestRequest()):
    if req.csv_path:
        df = load_csv(req.csv_path)
    else:
        df = load_csv(os.path.join(os.path.dirname(__file__), "data", "BTC_USDT_1d.csv"))

    return run_backtest(df)


@app.get("/db/trades")
def list_trades(limit: int = 50):
    conn = get_db()
    rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/db/snapshots")
def list_snapshots(limit: int = 50):
    conn = get_db()
    rows = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/live/start")
def live_start(req: LiveStartRequest = LiveStartRequest()):
    global _live_running, _live_thread

    if _live_running:
        return {"status": "already_running"}

    _live_running = True
    _live_thread = threading.Thread(
        target=_live_loop, args=(req.symbol, req.interval, req.dry_run), daemon=True
    )
    _live_thread.start()
    return {"status": "started", "symbol": req.symbol, "interval": req.interval, "dry_run": req.dry_run}


@app.post("/live/stop")
def live_stop():
    global _live_running
    _live_running = False
    return {"status": "stopped"}


@app.get("/live/status")
def live_status():
    return {"running": _live_running}


@app.get("/", response_class=HTMLResponse)



if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000)