from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Template
from schemas import TemplateResponse, TemplateDetailResponse

router = APIRouter(prefix="/api/templates", tags=["模板"])

@router.get("", response_model=List[TemplateResponse])
def list_templates(db: Session = Depends(get_db)):
    return db.query(Template).all()

@router.get("/{template_id}", response_model=TemplateDetailResponse)
def get_template(template_id: int, db: Session = Depends(get_db)):
    tpl = db.query(Template).filter(Template.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return tpl