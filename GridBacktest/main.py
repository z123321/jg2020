import base64
import io
import sqlite3
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

#aaaaaa
app = FastAPI(title="现货多+杠杆空｜本地SQLite回测OOP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========= 请修改为你的ohlcv数据库真实路径 =========
KLINE_DB_PATH = r"E:\pythonproject\exchangesdata\crypto_data.db"
# ====================================================

class GridStrategy:
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        grid_gap: float,
        leverage: float,
        long_ratio: float,
        stop_loss_pct: float,
        init_capital: float
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.start_date = start_date
        self.grid_gap = grid_gap
        self.leverage = leverage
        self.long_ratio = long_ratio
        self.stop_loss_pct = stop_loss_pct
        self.init_capital = init_capital

        self.trade_records = []

    def fetch_data(self) -> pd.DataFrame:
        start_dt = pd.to_datetime(self.start_date)
        start_ts_ms = int(start_dt.timestamp() * 1000)
        conn = sqlite3.connect(KLINE_DB_PATH)
        sql = """
        SELECT ts, open, high, low, close
        FROM ohlcv
        WHERE symbol = ? AND timeframe = ? AND ts >= ?
        ORDER BY ts ASC
        """
        df = pd.read_sql(sql, conn, params=(self.symbol, self.timeframe, start_ts_ms))
        conn.close()
        if len(df) == 0:
            raise Exception("本地数据库没有匹配的K线！检查交易对、周期、起始时间")
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        return df

    def run_backtest(self):
        df = self.fetch_data()
        if len(df) < 5:
            raise Exception("K线数量太少，至少5根K线才能回测")

        cash_total = self.init_capital
        cash_spot = cash_total * self.long_ratio
        cash_short = cash_total * (1 - self.long_ratio)

        spot_amount = 0.0
        short_size = 0.0
        short_entry_price = 0.0
        equity_list = []
        base_price = df["close"].iloc[0]

        for _, row in df.iterrows():
            close = row["close"]
            high = row["high"]
            low = row["low"]
            dt = row["ts"]

            down_grid_price = base_price * (1 - self.grid_gap)
            up_grid_price = base_price * (1 + self.grid_gap)

            # 现货买入
            if low <= down_grid_price and cash_spot > 10:
                buy_qty = (cash_spot * 0.1) / down_grid_price
                spot_amount += buy_qty
                cash_spot -= buy_qty * down_grid_price
                self.trade_records.append({
                    "dt": str(dt), "side": "SPOT_BUY", "price": down_grid_price,
                    "qty": buy_qty, "pnl": 0, "cash_spot": cash_spot,
                    "spot_amount": spot_amount, "cash_short": cash_short, "short_size": short_size
                })
                base_price = down_grid_price
            # 现货卖出
            if high >= up_grid_price and spot_amount > 0:
                sell_qty = spot_amount * 0.1
                pnl = sell_qty * (up_grid_price - base_price)
                spot_amount -= sell_qty
                cash_spot += sell_qty * up_grid_price
                self.trade_records.append({
                    "dt": str(dt), "side": "SPOT_SELL", "price": up_grid_price,
                    "qty": sell_qty, "pnl": pnl, "cash_spot": cash_spot,
                    "spot_amount": spot_amount, "cash_short": cash_short, "short_size": short_size
                })
                base_price = up_grid_price

            # 杠杆空单逻辑
            short_grid_up = base_price * (1 + self.grid_gap)
            short_grid_down = base_price * (1 - self.grid_gap)
            if high >= short_grid_up and cash_short > 10 and short_size <= 0:
                short_size = (cash_short * self.leverage * 0.1) / short_grid_up
                short_entry_price = short_grid_up
                self.trade_records.append({
                    "dt": str(dt), "side": "SHORT_OPEN", "price": short_grid_up,
                    "qty": short_size, "pnl": 0, "cash_spot": cash_spot,
                    "spot_amount": spot_amount, "cash_short": cash_short, "short_size": short_size
                })
            # 空单止损
            if short_size > 0:
                stop_price = short_entry_price * (1 + self.stop_loss_pct)
                if high >= stop_price:
                    pnl = short_size * (short_entry_price - stop_price)
                    cash_short += pnl
                    self.trade_records.append({
                        "dt": str(dt), "side": "SHORT_STOP", "price": stop_price,
                        "qty": short_size, "pnl": pnl, "cash_spot": cash_spot,
                        "spot_amount": spot_amount, "cash_short": cash_short, "short_size": 0
                    })
                    short_size = 0
            # 空单止盈平仓
            if short_size > 0 and low <= short_grid_down:
                pnl = short_size * (short_entry_price - short_grid_down)
                cash_short += pnl
                self.trade_records.append({
                    "dt": str(dt), "side": "SHORT_CLOSE", "price": short_grid_down,
                    "qty": short_size, "pnl": pnl, "cash_spot": cash_spot,
                    "spot_amount": spot_amount, "cash_short": cash_short, "short_size": 0
                })
                short_size = 0

            spot_value = spot_amount * close
            short_float_pnl = short_size * (short_entry_price - close) if short_size>0 else 0
            total_equity = cash_spot + spot_value + cash_short + short_float_pnl
            equity_list.append(total_equity)

        arr_eq = np.array(equity_list)
        final_eq = arr_eq[-1]
        ret_pct = (final_eq / self.init_capital - 1) * 100
        peak = np.maximum.accumulate(arr_eq)
        drawdown = (arr_eq - peak) / peak
        max_dd = float(np.min(drawdown)) * 100

        return {
            "final_equity": round(float(final_eq),2),
            "return_pct": round(float(ret_pct),2),
            "max_drawdown": round(float(max_dd),2),
            "equity_series": [float(x) for x in equity_list],
            "trade_list": self.trade_records
        }

    def plot_equity_base64(self, equity_series):
        fig, ax = plt.subplots(figsize=(10,5))
        ax.plot(equity_series, label=f"{self.symbol} 总权益(现货多+杠杆空)")
        ax.set_title(f"{self.symbol} | 杠杆{self.leverage} 现货资金占比{self.long_ratio}")
        ax.grid(alpha=0.3)
        ax.legend()
        buf = io.BytesIO()
        plt.tight_layout()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode("utf‑8")
        plt.close(fig)
        return img_b64


def init_db():
    conn = sqlite3.connect("backtest.db")
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS task (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        timeframe TEXT,
        start_date TEXT,
        grid_gap REAL,
        leverage REAL,
        long_ratio REAL,
        stop_loss_pct REAL,
        init_capital REAL,
        final_equity REAL,
        return_pct REAL,
        max_dd REAL,
        create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS trade (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        dt TEXT,
        side TEXT,
        price REAL,
        qty REAL,
        pnl REAL,
        cash_spot REAL,
        spot_amount REAL,
        cash_short REAL,
        short_size REAL
    )''')
    conn.commit()
    conn.close()

init_db()


class BacktestInput(BaseModel):
    symbol: str
    timeframe: str
    start_date: str
    grid_gap: float
    leverage: float
    long_ratio: float
    stop_loss_pct: float
    init_capital: float


@app.post("/run_backtest")
async def run_backtest(req: BacktestInput):
    try:
        strat = GridStrategy(
            symbol=req.symbol,
            timeframe=req.timeframe,
            start_date=req.start_date,
            grid_gap=req.grid_gap,
            leverage=req.leverage,
            long_ratio=req.long_ratio,
            stop_loss_pct=req.stop_loss_pct,
            init_capital=req.init_capital
        )
        res = strat.run_backtest()
        img64 = strat.plot_equity_base64(res["equity_series"])

        conn = sqlite3.connect("backtest.db")
        cur = conn.cursor()
        cur.execute('''INSERT INTO task
        (symbol,timeframe,start_date,grid_gap,leverage,long_ratio,stop_loss_pct,init_capital,final_equity,return_pct,max_dd)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (req.symbol, req.timeframe, req.start_date,
         req.grid_gap, req.leverage, req.long_ratio, req.stop_loss_pct,
         req.init_capital, res["final_equity"], res["return_pct"], res["max_drawdown"]))
        task_id = cur.lastrowid
        for t in res["trade_list"]:
            cur.execute('''INSERT INTO trade
            (task_id,dt,side,price,qty,pnl,cash_spot,spot_amount,cash_short,short_size)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (task_id, t["dt"], t["side"], t["price"], t["qty"], t["pnl"],
             t["cash_spot"], t["spot_amount"], t["cash_short"], t["short_size"]))
        conn.commit()
        conn.close()
        return {
            "task_id": task_id,
            "final_equity": res["final_equity"],
            "return_pct": res["return_pct"],
            "max_drawdown": res["max_drawdown"],
            "img_base64": img64
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/task_list")
async def get_task_list():
    conn = sqlite3.connect("backtest.db")
    df = pd.read_sql("SELECT * FROM task ORDER BY id DESC", conn)
    conn.close()
    return df.to_dict("records")


@app.get("/task_trades/{task_id}")
async def get_task_trades(task_id: int):
    conn = sqlite3.connect("backtest.db")
    df = pd.read_sql("SELECT * FROM trade WHERE task_id=?", conn, params=(task_id,))
    conn.close()
    return df.to_dict("records")


@app.get("/export_csv/{task_id}")
async def export_csv(task_id: int):
    conn = sqlite3.connect("backtest.db")
    df = pd.read_sql("SELECT * FROM trade WHERE task_id=?", conn, params=(task_id,))
    conn.close()
    out = io.StringIO()
    df.to_csv(out, index=False, encoding="utf‑8‑sig")
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content‑Disposition": f'attachment; filename="task_{task_id}_trade.csv"'}
    )


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r", encoding="utf‑8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
