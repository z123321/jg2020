import pymysql
import sqlite3
from pymysql.err import OperationalError


class BaseDBUtil:
    def create_database(self, db_name: str):
        raise NotImplementedError

    def create_table(self, db_name: str, table_name: str, col_def: dict):
        raise NotImplementedError

    def insert_one(self, db_name: str, table_name: str, data: dict):
        raise NotImplementedError

    def insert_batch(self, db_name: str, table_name: str, data_list: list[dict]):
        raise NotImplementedError

    def insert_duplicate(self, db_name: str, table_name: str, data: dict):
        raise NotImplementedError

    def insert_batch_duplicate(self, db_name: str, table_name: str, data_list: list[dict]):
        raise NotImplementedError

    def select(self, db_name: str, table_name: str, cols: list = None, where: dict = None, order_by: str = None, limit: int = None):
        raise NotImplementedError

    def select_page(self, db_name: str, table_name: str, cols: list = None, where: dict = None, order_by: str = None, page: int = 1, page_size: int = 20):
        raise NotImplementedError

    def update(self, db_name: str, table_name: str, set_data: dict, where: dict):
        raise NotImplementedError

    def delete(self, db_name: str, table_name: str, where: dict):
        raise NotImplementedError


class MysqlUtil(BaseDBUtil):
    def __init__(self, host: str, port: int, user: str, password: str, charset: str = "utf8mb4"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.charset = charset
        self.conn = None
        self.cur = None

    def connect(self, db_name: str = None):
        self.conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=db_name,
            charset=self.charset,
            autocommit=False
        )
        self.cur = self.conn.cursor()

    def close(self):
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()

    def create_database(self, db_name: str):
        self.connect()
        sql = f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        self.cur.execute(sql)
        self.conn.commit()
        self.close()

    def create_table(self, db_name: str, table_name: str, col_def: dict):
        self.connect(db_name)
        cols = []
        for k, v in col_def.items():
            cols.append(f"`{k}` {v}")
        col_sql = ", ".join(cols)
        sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` ({col_sql}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
        self.cur.execute(sql)
        self.conn.commit()
        self.close()

    def insert_one(self, db_name: str, table_name: str, data: dict):
        self.connect(db_name)
        keys = list(data.keys())
        placeholders = ["%s"] * len(keys)
        sql = f"""
        INSERT INTO `{table_name}` (`{"`,`".join(keys)}`)
        VALUES ({",".join(placeholders)})
        """
        vals = [data[k] for k in keys]
        self.cur.execute(sql, vals)
        self.conn.commit()
        last_id = self.cur.lastrowid
        self.close()
        return last_id

    def insert_batch(self, db_name: str, table_name: str, data_list: list[dict]):
        if not data_list:
            return 0
        self.connect(db_name)
        keys = list(data_list[0].keys())
        placeholders = ["%s"] * len(keys)
        sql = f"""
        INSERT INTO `{table_name}` (`{"`,`".join(keys)}`)
        VALUES ({",".join(placeholders)})
        """
        params = []
        for d in data_list:
            row = [d[k] for k in keys]
            params.append(row)
        cnt = self.cur.executemany(sql, params)
        self.conn.commit()
        self.close()
        return cnt

    def insert_duplicate(self, db_name: str, table_name: str, data: dict):
        self.connect(db_name)
        keys = list(data.keys())
        vals = [data[k] for k in keys]
        key_str = "`,`".join(keys)
        place = ",".join(["%s"] * len(keys))
        update_parts = [f"`{k}`=VALUES(`{k}`)" for k in keys]
        sql = f"""
        INSERT INTO `{table_name}` (`{key_str}`) VALUES ({place})
        ON DUPLICATE KEY UPDATE {",".join(update_parts)}
        """
        self.cur.execute(sql, vals)
        self.conn.commit()
        self.close()
        return self.cur.rowcount

    def insert_batch_duplicate(self, db_name: str, table_name: str, data_list: list[dict]):
        if not data_list:
            return 0
        self.connect(db_name)
        keys = list(data_list[0].keys())
        update_parts = [f"`{k}`=VALUES(`{k}`)" for k in keys]
        key_str = "`,`".join(keys)
        place = ",".join(["%s"] * len(keys))
        sql = f"""
        INSERT INTO `{table_name}` (`{key_str}`) VALUES ({place})
        ON DUPLICATE KEY UPDATE {",".join(update_parts)}
        """
        params = []
        for d in data_list:
            row = [d[k] for k in keys]
            params.append(row)
        cnt = self.cur.executemany(sql, params)
        self.conn.commit()
        self.close()
        return cnt

    def select(self, db_name: str, table_name: str, cols: list = None, where: dict = None, order_by: str = None, limit: int = None):
        self.connect(db_name)
        if cols is None:
            col_str = "*"
        else:
            col_str = "`" + "`,`".join(cols) + "`"
        sql = f"SELECT {col_str} FROM `{table_name}`"
        params = []
        if where and len(where) > 0:
            cond = []
            for k, v in where.items():
                cond.append(f"`{k}`=%s")
                params.append(v)
            sql += " WHERE " + " AND ".join(cond)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {limit}"
        self.cur.execute(sql, params)
        rows = self.cur.fetchall()
        desc = self.cur.description
        ret = []
        if desc:
            fields = [x[0] for x in desc]
            for r in rows:
                ret.append(dict(zip(fields, r)))
        self.close()
        return ret

    def select_page(self, db_name: str, table_name: str, cols: list = None, where: dict = None, order_by: str = None, page: int = 1, page_size: int = 20):
        if page < 1:
            page = 1
        offset = (page - 1) * page_size
        self.connect(db_name)
        if cols is None:
            col_str = "*"
        else:
            col_str = "`" + "`,`".join(cols) + "`"
        cond_parts = []
        params = []
        if where and len(where) > 0:
            for k, v in where.items():
                cond_parts.append(f"`{k}`=%s")
                params.append(v)
        where_sql = ""
        if cond_parts:
            where_sql = " WHERE " + " AND ".join(cond_parts)
        count_sql = f"SELECT COUNT(*) AS cnt FROM `{table_name}` {where_sql}"
        self.cur.execute(count_sql, params)
        total = self.cur.fetchone()[0]
        sql = f"SELECT {col_str} FROM `{table_name}` {where_sql}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        sql += f" LIMIT {offset}, {page_size}"
        self.cur.execute(sql, params)
        rows = self.cur.fetchall()
        desc = self.cur.description
        data_list = []
        if desc:
            fields = [x[0] for x in desc]
            for r in rows:
                data_list.append(dict(zip(fields, r)))
        self.close()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "list": data_list
        }

    def update(self, db_name: str, table_name: str, set_data: dict, where: dict):
        self.connect(db_name)
        set_parts = []
        params = []
        for k, v in set_data.items():
            set_parts.append(f"`{k}`=%s")
            params.append(v)
        where_parts = []
        for k, v in where.items():
            where_parts.append(f"`{k}`=%s")
            params.append(v)
        sql = f"""
        UPDATE `{table_name}`
        SET {",".join(set_parts)}
        WHERE {" AND ".join(where_parts)}
        """
        cnt = self.cur.execute(sql, params)
        self.conn.commit()
        self.close()
        return cnt

    def delete(self, db_name: str, table_name: str, where: dict):
        self.connect(db_name)
        cond = []
        params = []
        for k, v in where.items():
            cond.append(f"`{k}`=%s")
            params.append(v)
        sql = f"DELETE FROM `{table_name}` WHERE {' AND '.join(cond)}"
        cnt = self.cur.execute(sql, params)
        self.conn.commit()
        self.close()
        return cnt


class SqliteUtil(BaseDBUtil):
    def __init__(self):
        self.conn = None
        self.cur = None

    def connect(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()

    def close(self):
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()

    def create_database(self, db_name: str):
        pass

    def create_table(self, db_name: str, table_name: str, col_def: dict):
        self.connect(db_name)
        cols = []
        for k, v in col_def.items():
            cols.append(f"`{k}` {v}")
        col_sql = ", ".join(cols)
        sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` ({col_sql});"
        self.cur.execute(sql)
        self.conn.commit()
        self.close()

    def insert_one(self, db_name: str, table_name: str, data: dict):
        self.connect(db_name)
        keys = list(data.keys())
        placeholders = ["?"] * len(keys)
        sql = f"""
        INSERT INTO `{table_name}` (`{"`,`".join(keys)}`)
        VALUES ({",".join(placeholders)})
        """
        vals = [data[k] for k in keys]
        self.cur.execute(sql, vals)
        self.conn.commit()
        last_id = self.cur.lastrowid
        self.close()
        return last_id

    def insert_batch(self, db_name: str, table_name: str, data_list: list[dict]):
        if not data_list:
            return 0
        self.connect(db_name)
        keys = list(data_list[0].keys())
        placeholders = ["?"] * len(keys)
        sql = f"""
        INSERT INTO `{table_name}` (`{"`,`".join(keys)}`)
        VALUES ({",".join(placeholders)})
        """
        params = []
        for d in data_list:
            row = [d[k] for k in keys]
            params.append(row)
        cnt = self.cur.executemany(sql, params)
        self.conn.commit()
        self.close()
        return cnt

    def insert_duplicate(self, db_name: str, table_name: str, data: dict):
        self.connect(db_name)
        keys = list(data.keys())
        vals = [data[k] for k in keys]
        key_str = "`,`".join(keys)
        place = ",".join(["?"] * len(keys))
        update_parts = [f"`{k}`=excluded.`{k}`" for k in keys]
        sql = f"""
        INSERT INTO `{table_name}` (`{key_str}`) VALUES ({place})
        ON CONFLICT DO UPDATE SET {",".join(update_parts)}
        """
        self.cur.execute(sql, vals)
        self.conn.commit()
        self.close()
        return self.cur.rowcount

    def insert_batch_duplicate(self, db_name: str, table_name: str, data_list: list[dict]):
        if not data_list:
            return 0
        self.connect(db_name)
        keys = list(data_list[0].keys())
        update_parts = [f"`{k}`=excluded.`{k}`" for k in keys]
        key_str = "`,`".join(keys)
        place = ",".join(["?"] * len(keys))
        sql = f"""
        INSERT INTO `{table_name}` (`{key_str}`) VALUES ({place})
        ON CONFLICT DO UPDATE SET {",".join(update_parts)}
        """
        params = []
        for d in data_list:
            row = [d[k] for k in keys]
            params.append(row)
        cnt = self.cur.executemany(sql, params)
        self.conn.commit()
        self.close()
        return cnt

    def select(self, db_name: str, table_name: str, cols: list = None, where: dict = None, order_by: str = None, limit: int = None):
        self.connect(db_name)
        if cols is None:
            col_str = "*"
        else:
            col_str = "`" + "`,`".join(cols) + "`"
        sql = f"SELECT {col_str} FROM `{table_name}`"
        params = []
        if where and len(where) > 0:
            cond = []
            for k, v in where.items():
                cond.append(f"`{k}`=?")
                params.append(v)
            sql += " WHERE " + " AND ".join(cond)
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {limit}"
        self.cur.execute(sql, params)
        rows = self.cur.fetchall()
        ret = []
        for r in rows:
            ret.append(dict(r))
        self.close()
        return ret

    def select_page(self, db_name: str, table_name: str, cols: list = None, where: dict = None, order_by: str = None, page: int = 1, page_size: int = 20):
        if page < 1:
            page = 1
        offset = (page - 1) * page_size
        self.connect(db_name)
        if cols is None:
            col_str = "*"
        else:
            col_str = "`" + "`,`".join(cols) + "`"
        cond_parts = []
        params = []
        if where and len(where) > 0:
            for k, v in where.items():
                cond_parts.append(f"`{k}`=?")
                params.append(v)
        where_sql = ""
        if cond_parts:
            where_sql = " WHERE " + " AND ".join(cond_parts)
        count_sql = f"SELECT COUNT(*) AS cnt FROM `{table_name}` {where_sql}"
        self.cur.execute(count_sql, params)
        total = self.cur.fetchone()[0]
        sql = f"SELECT {col_str} FROM `{table_name}` {where_sql}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        sql += f" LIMIT {offset}, {page_size}"
        self.cur.execute(sql, params)
        rows = self.cur.fetchall()
        data_list = [dict(r) for r in rows]
        self.close()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "list": data_list
        }

    def update(self, db_name: str, table_name: str, set_data: dict, where: dict):
        self.connect(db_name)
        set_parts = []
        params = []
        for k, v in set_data.items():
            set_parts.append(f"`{k}`=?")
            params.append(v)
        where_parts = []
        for k, v in where.items():
            where_parts.append(f"`{k}`=?")
            params.append(v)
        sql = f"""
        UPDATE `{table_name}`
        SET {",".join(set_parts)}
        WHERE {" AND ".join(where_parts)}
        """
        cnt = self.cur.execute(sql, params)
        self.conn.commit()
        self.close()
        return cnt

    def delete(self, db_name: str, table_name: str, where: dict):
        self.connect(db_name)
        cond = []
        params = []
        for k, v in where.items():
            cond.append(f"`{k}`=?")
            params.append(v)
        sql = f"DELETE FROM `{table_name}` WHERE {' AND '.join(cond)}"
        cnt = self.cur.execute(sql, params)
        self.conn.commit()
        self.close()
        return cnt


def get_db_instance(cfg: dict) -> BaseDBUtil:
    """
    工厂方法，根据配置自动返回实例
    cfg示例：
    # mysql
    {
        "db_type": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "user": "root",
        "password": "123456"
    }
    # sqlite
    {
        "db_type": "sqlite"
    }
    """
    t = cfg.get("db_type", "sqlite")
    if t == "mysql":
        return MysqlUtil(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            charset=cfg.get("charset", "utf8mb4")
        )
    elif t == "sqlite":
        return SqliteUtil()
    else:
        raise ValueError(f"不支持的 db_type: {t}")
def migrate_table(source_cfg: dict, target_cfg: dict, source_db_name: str, target_db_name: str, table_name: str, where: dict = None, page_size: int = 2000):
    """
    表数据迁移，支持 sqlite <-> mysql
    :param source_cfg: 源库配置，传给get_db_instance
    :param target_cfg: 目标库配置
    :param source_db_name: 源库名（sqlite是文件路径）
    :param target_db_name: 目标库名（sqlite是文件路径）
    :param table_name: 表名
    :param where: 过滤条件dict，如 {"exchange":"okx","symbol":"BTC/USDT"}
    :param page_size: 每次读取条数，ohlcv建议2000~5000
    :return: 迁移总条数
    """
    src_db = get_db_instance(source_cfg)
    dst_db = get_db_instance(target_cfg)

    page = 1
    total_migrated = 0
    while True:
        ret = src_db.select_page(
            db_name=source_db_name,
            table_name=table_name,
            cols=None,
            where=where,
            order_by=None,
            page=page,
            page_size=page_size
        )
        data_list = ret["list"]
        if not data_list:
            break
        cnt = dst_db.insert_batch_duplicate(target_db_name, table_name, data_list)
        total_migrated += cnt
        page += 1
    return total_migrated
