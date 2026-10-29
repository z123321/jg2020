from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
from models import BacktestJob, Strategy, User
from schemas import BacktestCreate, BacktestResponse, MessageResponse
from middleware import get_current_user

router = APIRouter(prefix="/api/backtest", tags=["回测"])

@router.post("", response_model=BacktestResponse)
def create_backtest(body: BacktestCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not body.risk_acknowledged:
        raise HTTPException(status_code=400, detail="请确认风险声明")

    strategy = db.query(Strategy).filter(Strategy.id == body.strategy_id, Strategy.user_id == user.id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    if user.quota_backtest <= 0:
        raise HTTPException(status_code=403, detail="回测次数不足，请升级套餐")

    job = BacktestJob(
        user_id=user.id,
        strategy_id=body.strategy_id,
        symbols=body.symbols,
        start_date=body.start_date,
        end_date=body.end_date,
        initial_capital=body.initial_capital,
        status="pending",
    )
    db.add(job)

    # 扣减配额
    user.quota_backtest -= 1
    db.commit()
    db.refresh(job)
    return job

@router.get("/{job_id}", response_model=BacktestResponse)
def get_backtest(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(BacktestJob).filter(BacktestJob.id == job_id, BacktestJob.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job

@router.get("/{job_id}/export", response_model=MessageResponse)
def export_backtest(job_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.query(BacktestJob).filter(BacktestJob.id == job_id, BacktestJob.user_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != "done" or not job.result_json:
        raise HTTPException(status_code=400, detail="回测未完成或无结果")
    # TODO: 生成 PDF/Excel 文件并返回下载链接
    return {"message": "导出功能开发中"}