import sqlite3
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from datetime import datetime
import sys

# ========= 解决中文乱码 =========
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
# ================================

DB_FILE = "binance_heat.db"
SHOW_HOURS = 4
REFRESH_INTERVAL = 2000

if len(sys.argv) < 2:
    print("用法：python plot_heat_realtime.py dogeusdt")
    sys.exit(0)
SYMBOL = sys.argv[1].lower()
TABLE_NAME = f"snap_{SYMBOL}"

fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

def load_latest():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cutoff = int(datetime.now().timestamp()) - SHOW_HOURS * 3600
    cur.execute(f"""
        SELECT ts, price, heat, buy_vol, sell_vol
        FROM {TABLE_NAME}
        WHERE ts >= ?
        ORDER BY ts ASC;
    """, (cutoff,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return [], [], [], [], []
    ts, price, heat, buy_vol, sell_vol = zip(*rows)
    x_time = [datetime.fromtimestamp(t) for t in ts]
    return x_time, price, heat, buy_vol, sell_vol

def update(frame):
    ax1.clear()
    ax2.clear()
    ax3.clear()
    x, price, heat, buyv, sellv = load_latest()
    if len(x) == 0:
        ax1.set_title(f"{SYMBOL.upper()} - 等待采集数据，请先启动采集程序")
        return
    # 价格
    ax1.plot(x, price, color="#2277dd", linewidth=1.2, label="价格")
    ax1.set_title(f"{SYMBOL.upper()} 实时行情 | 最近{SHOW_HOURS}小时")
    ax1.set_ylabel("价格")
    ax1.grid(alpha=0.3)
    ax1.legend()
    # 热度
    ax2.plot(x, heat, color="#dd4422", linewidth=1.3, label="热度")
    ax2.axhline(y=0.3, color='#ff2222', linestyle='--', alpha=0.6, label="空头阈值0.3")
    ax2.axhline(y=0.7, color='#22bb22', linestyle='--', alpha=0.6, label="多头阈值0.7")
    ax2.set_ylim(0, 1)
    ax2.set_ylabel("热度 0~1")
    ax2.grid(alpha=0.3)
    ax2.legend()
    # 成交量
    width = 0.0006
    ax3.bar(x, buyv, width=width, color="#33bb55", alpha=0.7, label="主动买量")
    ax3.bar(x, [-v for v in sellv], width=width, color="#dd3333", alpha=0.7, label="主动卖量")
    ax3.set_ylabel("成交量")
    ax3.set_xlabel("时间")
    ax3.grid(alpha=0.3)
    ax3.legend()
    fig.tight_layout()

if __name__ == "__main__":
    ani = FuncAnimation(fig, update, interval=REFRESH_INTERVAL, cache_frame_data=False)
    plt.show()
