import sqlite3
import os
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import Workbook
from io import BytesIO
from datetime import datetime
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "workshop.db")
MAX_RECORD = 500
print("数据库完整路径：", DB_FILE)

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # 表结构，包含create_time录入时间
    cur.execute("""
    CREATE TABLE IF NOT EXISTS production_record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        work_order TEXT,
        product_name TEXT,
        batch_no TEXT,
        produce_date TEXT,
        quantity INTEGER,
        operator TEXT,
        remark TEXT,
        create_time TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_work_order ON production_record(work_order);")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        real_name TEXT
    )
    """)
    # 初始化admin账号
    cur.execute("SELECT * FROM user WHERE username = ?", ("admin",))
    row = cur.fetchone()
    if not row:
        hash_pwd = generate_password_hash("admin123")
        cur.execute("INSERT INTO user(username,password,real_name) VALUES (?,?,?)", ("admin", hash_pwd, "管理员"))
    conn.commit()
    conn.close()
    print("✅ init_db执行完毕")

def get_total_record():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM production_record")
    total = cur.fetchone()["cnt"]
    conn.close()
    return total

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM user WHERE username = ?", (username,))
    user = cur.fetchone()
    conn.close()
    if user and check_password_hash(user["password"], password):
        return jsonify({"msg":"登录成功", "user": dict(user)})
    return jsonify({"msg":"账号或密码错误"}),400

@app.route("/api/production", methods=["GET"])
def get_list():
    work_order = request.args.get("work_order","")
    page = int(request.args.get("page",1))
    page_size = int(request.args.get("page_size",10))
    offset = (page-1)*page_size
    conn = get_conn()
    cur = conn.cursor()
    if work_order:
        sql = "SELECT * FROM production_record WHERE work_order LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?"
        cur.execute(sql, (f"%{work_order}%", page_size, offset))
    else:
        sql = "SELECT * FROM production_record ORDER BY id DESC LIMIT ? OFFSET ?"
        cur.execute(sql, (page_size, offset))
    rows = cur.fetchall()
    data = [dict(r) for r in rows]
    total = get_total_record()
    conn.close()
    return jsonify({"list":data, "total":total})

@app.route("/api/production", methods=["POST"])
def add_record():
    total = get_total_record()
    if total >= MAX_RECORD:
        return jsonify({"msg":f"已达到免费版上限{MAX_RECORD}条，请升级商业版"}),400
    data = request.get_json()
    work_order = data.get("work_order")
    product_name = data.get("product_name")
    batch_no = data.get("batch_no")
    produce_date = data.get("produce_date")
    quantity = data.get("quantity")
    operator = data.get("operator")
    remark = data.get("remark","")
    create_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO production_record
    (work_order,product_name,batch_no,produce_date,quantity,operator,remark,create_time)
    VALUES (?,?,?,?,?,?,?,?)
    """, (work_order,product_name,batch_no,produce_date,quantity,operator,remark,create_time))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"msg":"新增成功","id":new_id})

@app.route("/api/production/<int:rid>", methods=["PUT"])
def edit_record(rid):
    data = request.get_json()
    work_order = data.get("work_order")
    product_name = data.get("product_name")
    batch_no = data.get("batch_no")
    produce_date = data.get("produce_date")
    quantity = data.get("quantity")
    operator = data.get("operator")
    remark = data.get("remark","")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    UPDATE production_record SET
    work_order=?,product_name=?,batch_no=?,produce_date=?,quantity=?,operator=?,remark=?
    WHERE id=?
    """, (work_order,product_name,batch_no,produce_date,quantity,operator,remark,rid))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    if affected>0:
        return jsonify({"msg":"修改成功"})
    return jsonify({"msg":"找不到记录"}),400

@app.route("/api/production/<int:rid>", methods=["DELETE"])
def del_record(rid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM production_record WHERE id=?", (rid,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    if affected>0:
        return jsonify({"msg":"删除成功"})
    return jsonify({"msg":"找不到记录"}),400

@app.route("/api/production/export", methods=["GET"])
def export():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM production_record ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "生产台账"
    headers = ["ID","工单编号","产品名称","批次号","生产日期","生产数量","操作员","备注","录入时间"]
    ws.append(headers)
    for r in rows:
        row_dict = dict(r)
        ws.append([
            row_dict["id"],
            row_dict["work_order"],
            row_dict["product_name"],
            row_dict["batch_no"],
            row_dict["produce_date"],
            row_dict["quantity"],
            row_dict["operator"],
            row_dict["remark"],
            row_dict.get("create_time", "")
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    resp = make_response(output.read())
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    filename = quote("生产台账.xlsx")
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{filename}"
    return resp

if __name__ == "__main__":
    init_db()
    print("✅ 服务启动：http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
