from db_util import get_db_instance

cfg = {
    "db_type": "sqlite"
}
db = get_db_instance(cfg)

table_def = {
    "exchange":"VARCHAR(32) NOT NULL",
    "symbol":"VARCHAR(32) NOT NULL",
    "ts":"BIGINT NOT NULL PRIMARY KEY",
    "close":"DOUBLE"
}
db.create_table("test.db", "ohlcv", table_def)

db.insert_duplicate("test.db", "ohlcv", {
    "exchange":"okx",
    "symbol":"BTC/USDT",
    "ts":1700000000000,
    "close":40000
})

data = db.select_page(
    "test.db",
    "ohlcv",
    where={"symbol":"BTC/USDT"},
    page=1,
    page_size=10
)
print(data)