# -*- coding: utf-8 -*-
"""
支付宝 + 微信账单自动分析 + 预算对比脚本
用法：python bill_analyzer.py 支付宝.csv 微信.xlsx
"""
import sys
import pandas as pd

# 分类规则：关键词 → 类别
CATEGORY_RULES = {
    "餐饮": ["外卖", "美团", "饿了么", "餐饮", "小吃", "快餐", "饭店", "麻辣烫", "牛肉",
             "面", "饭", "粥", "包子", "馒头", "饺子", "便利店", "7-11", "全家",
             "罗森", "麦当劳", "肯德基", "汉堡", "烧烤", "火锅", "奶茶", "咖啡", "瑞幸",
             "卤肉", "馄饨", "沙茶面", "炸鸡", "烤翅", "包子铺", "闪购",
             "黄焖鸡", "冒菜", "干锅", "鲜又好"],
    "交通": ["地铁", "公交", "打车", "滴滴", "高德", "曹操", "T3", "加油", "充电",
             "停车", "高铁", "火车票", "机票", "12306", "共享单车", "哈啰", "美团单车",
             "电动车", "电瓶", "绿沃新能源"],
    "购物": ["淘宝", "天猫", "京东", "拼多多", "抖音商城", "唯品会", "苏宁", "国美",
             "永辉", "大润发", "沃尔玛", "盒马", "超市", "鲜又好超市"],
    "烟": ["烟草", "烟", "美宜佳", "美宜家", "无人售货", "圩隆创"],
    "娱乐": ["电影", "游戏", "腾讯视频", "爱奇艺", "优酷", "芒果", "B站", "网易云",
             "QQ音乐", "Steam", "王者荣耀", "原神", "抖音", "快手"],
    "通讯": ["话费", "移动", "联通", "电信", "宽带"],
    "住房": ["房租", "水电", "燃气", "物业", "电费", "水费", "青山"],
    "保险": ["保险", "人保", "健康保险", "好医保"],
    "转账": ["发给", "转账", "红包"],
}

# 你的月度预算（元）
BUDGET = {
    "餐饮": 550,
    "交通": 30,
    "购物": 200,
    "烟": 330,
    "娱乐": 220,
    "通讯": 100,
    "住房": 900,
}

def classify(merchant, item):
    text = str(merchant) + str(item)
    for category, keywords in CATEGORY_RULES.items():
        for kw in keywords:
            if kw in text:
                return category
    return "其他"

def load_alipay_csv(path):
    """读支付宝CSV账单"""
    df = pd.read_csv(path, skiprows=23, encoding="gbk")
    df = df[df["收/支"] == "支出"]
    df["金额"] = df["金额"].astype(float)
    df["类别"] = df.apply(lambda row: classify(row["交易对方"], row["商品说明"]), axis=1)
    return df[["金额", "类别"]]

def load_wechat_xlsx(path):
    """读微信xlsx账单"""
    df = pd.read_excel(path, header=None, skiprows=17)
    df.columns = ["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
                  "支付方式", "当前状态", "交易单号", "商户单号", "备注"]
    df = df[df["收/支"] == "支出"]
    df["金额"] = df["金额(元)"].astype(float)
    df["类别"] = df.apply(lambda row: classify(row["交易对方"], row["商品"]), axis=1)
    return df[["金额", "类别"]]

def analyze(files):
    dfs = []
    for f in files:
        if f.lower().endswith(".xlsx"):
            dfs.append(load_wechat_xlsx(f))
        else:
            dfs.append(load_alipay_csv(f))

    df = pd.concat(dfs)
    summary = df.groupby("类别")["金额"].sum().to_dict()

    print("=" * 55)
    print(f"📊 9月账单分析 + 预算对比（支付宝+微信合并）")
    print("=" * 55)
    print(f"{'类别':<8} {'预算':>8} {'实际':>8} {'差额':>8} {'状态':<6}")
    print("-" * 55)

    total_budget = 0
    total_actual = 0

    for cat, budget in BUDGET.items():
        actual = summary.get(cat, 0)
        diff = budget - actual
        status = "✅ 省" if diff >= 0 else "❌ 超"
        print(f"{cat:<8} {budget:>8.0f} {actual:>8.2f} {diff:>8.2f} {status:<6}")
        total_budget += budget
        total_actual += actual

    other = sum(v for k, v in summary.items() if k not in BUDGET)
    if other > 0:
        print(f"{'其他':<8} {'-':>8} {other:>8.2f} {'-':>8} {'⚠️ 未分类':<6}")
        total_actual += other

    print("-" * 55)
    print(f"{'合计':<8} {total_budget:>8.0f} {total_actual:>8.2f} {total_budget-total_actual:>8.2f}")
    print("=" * 55)

    if total_actual > total_budget:
        print(f"⚠️ 本月超支了 {total_actual - total_budget:.2f} 元，注意控制！")
    else:
        print(f"✅ 本月结余 {total_budget - total_actual:.2f} 元，继续保持！")

    print(f"\n📌 账单总支出：{total_actual:.2f} 元")
    print(f"📌 固定支出（还贷2700+房租900+话费100）：3700 元")
    print(f"📌 可支配预算：1800 元")
    print(f"📌 应急金目标：590 元")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python bill_analyzer.py 支付宝.csv [微信.xlsx]")
        sys.exit(1)
    analyze(sys.argv[1:])
#（注：内容由AI生成）
