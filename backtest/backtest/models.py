from sqlalchemy import Column, BigInteger, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey, DECIMAL, Date
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True)
    phone = Column(String(20), unique=True)
    password_hash = Column(String(255), nullable=False)
    plan = Column(String(20), default="free")
    quota_backtest = Column(Integer, default=5)
    quota_data_mb = Column(Integer, default=100)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Strategy(Base):
    __tablename__ = "strategies"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    type = Column(String(30), nullable=False)
    config = Column(JSON, nullable=False)
    template_id = Column(BigInteger)
    is_public = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

class BacktestJob(Base):
    __tablename__ = "backtest_jobs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    strategy_id = Column(BigInteger, ForeignKey("strategies.id"), nullable=False)
    symbols = Column(JSON, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    initial_capital = Column(DECIMAL(18, 2), default=100000)
    status = Column(String(20), default="pending")
    result_json = Column(JSON)
    error_msg = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

class Template(Base):
    __tablename__ = "templates"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    type = Column(String(30), nullable=False)
    description = Column(Text)
    config_schema = Column(JSON, nullable=False)
    code_template = Column(Text, nullable=False)
    price = Column(DECIMAL(10, 2), default=0)
    is_free = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

class Order(Base):
    __tablename__ = "orders"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    product_type = Column(String(30), nullable=False)
    product_id = Column(BigInteger)
    amount = Column(DECIMAL(10, 2), nullable=False)
    status = Column(String(20), default="pending")
    pay_channel = Column(String(20))
    trade_no = Column(String(100))
    paid_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())