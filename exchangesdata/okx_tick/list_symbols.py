import sqlite3
import re
from datetime import datetime

DB_FILE = "binance_heat.db"

def list_collected_symbols():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    # 查询所有数据表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    all_tables = cur.fetchall()
    print("==== 数据库内已采集币种列表 ====\n")
    found = False
    for (tbl,) in all_tables:
        match = re.match(r"snap_(\w+)", tbl)
        if not match:
            continue
        found = True
        sym = match.group(1)
        cur.execute(f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM {tbl}")
        cnt, ts_min, ts_max = cur.fetchone()
        if cnt == 0:
            time_range = "无数据"
        else:
            t_start = datetime.fromtimestamp(ts_min).strftime('%Y-%m-%d %H:%M:%S')
            t_end = datetime.fromtimestamp(ts_max).strftime('%Y-%m-%d %H:%M:%S')
            time_range = f"{t_start} ~ {t_end}"
        print(f"【{sym.upper()}】 记录数：{cnt} | 时间范围：{time_range}")
    if not found:
        print("暂无任何币种快照数据，请先运行采集程序！")
    conn.close()
    print("\n==============================")

if __name__ == "__main__":
    list_collected_symbols()
