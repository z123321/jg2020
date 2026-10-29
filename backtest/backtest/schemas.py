from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import date, datetime
from decimal import Decimal

# --- User ---
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    email: Optional[str] = None
    phone: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    plan: str
    quota_backtest: int
    quota_data_mb: int

    class Config:
        from_attributes = True

# --- Strategy ---
class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str  # fixed_invest / grid / long_short / multi_stock / custom
    config: Dict[str, Any]
    template_id: Optional[int] = None
    is_public: bool = False

class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_public: Optional[bool] = None

class StrategyResponse(BaseModel):
    id: int
    user_id: int
    name: str
    type: str
    config: Dict[str, Any]
    template_id: Optional[int]
    is_public: bool
    created_at: datetime

    class Config:
        from_attributes = True

# --- Backtest ---
class BacktestCreate(BaseModel):
    strategy_id: int
    symbols: List[str]
    start_date: date
    end_date: date
    initial_capital: float = 100000
    risk_acknowledged: bool = Field(..., description="用户已阅读并确认风险声明")

class BacktestResponse(BaseModel):
    id: int
    user_id: int
    strategy_id: int
    symbols: List[str]
    start_date: date
    end_date: date
    initial_capital: Decimal
    status: str
    result_json: Optional[Dict[str, Any]]
    error_msg: Optional[str]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True

# --- Template ---
class TemplateResponse(BaseModel):
    id: int
    name: str
    type: str
    description: Optional[str]
    price: Decimal
    is_free: bool

    class Config:
        from_attributes = True

class TemplateDetailResponse(TemplateResponse):
    config_schema: Dict[str, Any]
    code_template: str

    class Config:
        from_attributes = True

# --- Order ---
class OrderCreate(BaseModel):
    product_type: str  # subscription / data_pack / template / deploy
    product_id: Optional[int] = None
    pay_channel: str = "wechat"  # wechat / alipay

class OrderResponse(BaseModel):
    id: int
    user_id: int
    product_type: str
    amount: Decimal
    status: str
    pay_channel: Optional[str]
    trade_no: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# --- Common ---
class MessageResponse(BaseModel):
    message: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"