from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Strategy, User
from schemas import StrategyCreate, StrategyUpdate, StrategyResponse, MessageResponse
from middleware import get_current_user

router = APIRouter(prefix="/api/strategies", tags=["策略"])

@router.get("", response_model=List[StrategyResponse])
def list_strategies(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Strategy).filter(Strategy.user_id == user.id).all()

@router.post("", response_model=StrategyResponse)
def create_strategy(body: StrategyCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    strategy = Strategy(
        user_id=user.id,
        name=body.name,
        type=body.type,
        config=body.config,
        template_id=body.template_id,
        is_public=body.is_public,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return strategy

@router.put("/{strategy_id}", response_model=StrategyResponse)
def update_strategy(strategy_id: int, body: StrategyUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user.id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    if body.name is not None:
        strategy.name = body.name
    if body.config is not None:
        strategy.config = body.config
    if body.is_public is not None:
        strategy.is_public = body.is_public
    db.commit()
    db.refresh(strategy)
    return strategy

@router.delete("/{strategy_id}", response_model=MessageResponse)
def delete_strategy(strategy_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user.id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    db.delete(strategy)
    db.commit()
    return {"message": "已删除"}