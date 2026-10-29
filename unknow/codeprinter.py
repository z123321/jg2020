import time
import random
from datetime import datetime
import string

# 随机生成字符串
def random_str(length=8):
    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

def random_hex():
    # 模拟内存地址/哈希
    return "0x" + ''.join(random.choice("0123456789abcdef") for _ in range(12))

# 日志模板池
log_tpl = [
    "load module [{}], mem={}",
    "request id:{} status={}, cost {}ms",
    "parse json data -> {{code:{},msg:'success'}}",
    "cache set key={}, expire={}s",
    "connect db host [127.0.0.1:3306], session={}",
    "thread[{}] working, queue remain:{}",
    "read file block {}, size {} KB",
    "udp packet recv, src={}, checksum={}",
    "algorithm calc finished, result = {}",
    "pool status idle:{}, active:{}"
]

status_code = [200,201,400,404,500,206]

print("==== Service Start, Loading resources ====\n",flush=True)
time.sleep(1)

while True:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level = random.choice(["INFO","DEBUG","TRACE"])
    tpl = random.choice(log_tpl)
    
    # 填充模板随机参数
    line = tpl.format(
        random_str(),
        random_hex(),
        random.choice(status_code),
        random.randint(1,1200),
        random_str(10),
        random.randint(30,3600),
        random_str(6),
        random.randint(0,500),
        random.randint(1,999),
        random_hex(),
        random_hex(),
        random.randint(1,9999),
        random.randint(0,20),
        random.randint(0,100)
    )
    print(f"[{now}] [{level}] {line}",flush=True)
    time.sleep(0.05) # 越小滚动越快，0.08速度很丝滑
