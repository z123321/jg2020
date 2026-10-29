from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import Order, User
from schemas import OrderCreate, OrderResponse, MessageResponse
from middleware import get_current_user

router = APIRouter(prefix="/api/payment", tags=["支付"])

@router.post("/create", response_model=OrderResponse)
def create_order(body: OrderCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # TODO: 根据 product_type / product_id 计算金额
    amount = 0.0  # 占位，实际应从商品表查询
    order = Order(
        user_id=user.id,
        product_type=body.product_type,
        product_id=body.product_id,
        amount=amount,
        pay_channel=body.pay_channel,
        status="pending",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # TODO: 调用微信支付/支付宝统一下单接口，获取支付参数
    # 这里先返回订单，前端拿到订单号后调起支付
    return order

@router.post("/callback/wechat", response_model=MessageResponse)
def wechat_callback(request: Request, db: Session = Depends(get_db)):
    # TODO: 验证微信签名，更新订单状态
    return {"message": "ok"}

@router.post("/callback/alipay", response_model=MessageResponse)
def alipay_callback(request: Request, db: Session = Depends(get_db)):
    # TODO: 验证支付宝签名，更新订单状态
    return {"message": "ok"}