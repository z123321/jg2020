# strategies/ma_strategy.py
from datetime import datetime

class MAStrategy:
    """双均线策略"""
    name = "双均线策略" # <--- 加上这一行
    def __init__(self, fast_period=5, slow_period=20):
        self.fast_period = fast_period
        self.slow_period = slow_period
    
    def run(self, close, total_usdt, total_btc):
        current_equity = total_usdt + (total_btc * close)
        # ... 你原来的双均线逻辑 ...
        return {
            "signal": signal,
            "message": message,
            "equity": round(current_equity, 2),
        }