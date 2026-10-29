from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import User
from schemas import UserResponse
from middleware import get_current_user

router = APIRouter(prefix="/api/user", tags=["用户"])

@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    return user