# ========== 1. Python list（对应Java ArrayList）==========
print("===== List 演示 =====")
my_list = ["苹果", "香蕉", "橙子"]
print("初始列表：", my_list)

my_list.append("葡萄")   # Java list.add()，尾部追加
print("append添加葡萄：", my_list)

print("取下标0元素：", my_list[0]) # 取值

my_list[1] = "榴莲"      # 修改下标1的元素
print("修改下标1为榴莲：", my_list)

my_list.pop(0)           # 删除下标0元素，Java list.remove(0)
print("pop删除下标0：", my_list)

print("for直接遍历：")
for item in my_list:
    print(item)

print("enumerate带下标遍历：")
for idx, item in enumerate(my_list):
    print(f"下标:{idx}, 值:{item}")


# ========== 2. Python dict（对应Java HashMap）==========
print("\n===== Dict 演示 =====")
my_dict = {"name":"老王", "age":30, "city":"厦门"}
print("原始字典：", my_dict)

my_dict["job"] = "操作员"   # put，新增key
print("新增job：", my_dict)

my_dict["age"] = 40        # 修改key，key重复会覆盖
print("修改age：", my_dict)

# 两种取值方式
print("直接取name：", my_dict["name"])
print("get安全取值（不存在返回None）：", my_dict.get("phone"))

my_dict.pop("city")        # 删除key
print("删除city：", my_dict)

print("\n遍历key+value（最常用，对应Java entrySet）")
for k, v in my_dict.items():
    print(f"key={k}, value={v}")

print("\n只遍历key")
for k in my_dict:
    print(k)

print("\n只遍历value")
for v in my_dict.values():
    print(v)
