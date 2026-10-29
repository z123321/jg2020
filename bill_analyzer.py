# -*- coding: utf-8 -*-
import sys
import pandas as pd

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

BUDGET = {"餐饮": 550, "交通": 30, "购物": 200, "烟": 330,
          "娱乐": 220, "通讯": 100, "住房": 900}

def classify(text):
    for cat, kws in CATEGORY_RULES.items():
        for kw in kws:
            if kw in text:
                return cat
    return "其他"

def load_alipay(path):
    df = pd.read_csv(path, encoding='gbk', skiprows=23)
    df = df[df['收/支'] == '支出'].copy()
    df['金额'] = pd.to_numeric(df['金额'], errors='coerce')
    df['名称'] = df['交易对方'].astype(str) + df['商品说明'].astype(str)
    df['类别'] = df['名称'].apply(classify)
    df['来源'] = '支付宝'
    return df[['交易时间','类别','名称','金额','来源']]

def load_wechat(path):
    df = pd.read_excel(path, header=17)
    amt = '金额(元)' if '金额(元)' in df.columns else '金额'
    item = '商品' if '商品' in df.columns else '商品说明'
    df = df[df['收/支'] == '支出'].copy()
    df['金额'] = pd.to_numeric(df[amt], errors='coerce')
    df['名称'] = df['交易对方'].astype(str) + df[item].astype(str)
    df['类别'] = df['名称'].apply(classify)
    df['来源'] = '微信'
    return df[['交易时间','类别','名称','金额','来源']]

def main():
    if len(sys.argv) < 3:
        print("用法: python bill_analyzer.py alipay.csv wechat.xlsx")
        sys.exit(1)

    print("正在读取账单...")
    ali = load_alipay(sys.argv[1])
    wx = load_wechat(sys.argv[2])
    df = pd.concat([ali, wx], ignore_index=True)

    summary = df.groupby('类别')['金额'].sum()
    non_budget = df[~df['类别'].isin(BUDGET.keys())]['金额'].sum()

    print("")
    print("=" * 55)
    print("9月账单分析 + 预算对比")
    print("=" * 55)
    print("%-8s%8s%10s%10s%6s" % ("类别","预算","实际","差额","状态"))
    print("-" * 55)

    total_bud = 0
    total_real = 0
    for cat, bud in BUDGET.items():
        real = round(float(summary.get(cat, 0)), 2)
        diff = round(bud - real, 2)
        status = "结余" if diff >= 0 else "超支"
        print("%-8s%8.0f%10.2f%10.2f%6s" % (cat, bud, real, diff, status))
        total_bud += bud
        total_real += real

    if non_budget > 0:
        print("%-8s%8s%10.2f" % ("其他", "-", non_budget))
        total_real += non_budget

    print("-" * 55)
    diff = round(total_bud - total_real, 2)
    print("%-8s%8.0f%10.2f%10.2f" % ("合计", total_bud, total_real, diff))
    print("=" * 55)

    if diff < 0:
        print("【警告】本月超支 %.2f 元" % abs(diff))
    else:
        print("【正常】本月结余 %.2f 元" % diff)

    print("")
    print("总支出: %.2f 元  共 %d 笔" % (total_real, len(df)))

if __name__ == "__main__":
    main()
