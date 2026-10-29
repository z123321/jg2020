from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from routers import auth, user, strategies, backtest, templates, payment

Base.metadata.create_all(bind=engine)

app = FastAPI(title="量化回测工具站", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境改成前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(user.router)
app.include_router(strategies.router)
app.include_router(backtest.router)
app.include_router(templates.router)
app.include_router(payment.router)

@app.get("/health")
def health():
    return {"status": "ok"}