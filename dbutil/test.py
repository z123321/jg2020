from db_util import get_db_instance

src_cfg = {"db_type": "sqlite"}
dst_cfg = {
    "db_type": "mysql",
    "host":"127.0.0.1",
    "port":3306,
    "user":"root",
    "password":"123456"
}

total = migrate_table(
    source_cfg=src_cfg,
    target_cfg=dst_cfg,
    source_db_name="crypto.db",
    target_db_name="crypto_db",
    table_name="ohlcv", 
    page_size=2000
)
print(f"迁移总数：{total}")