# strategies/grid_strategy.py
from datetime import datetime

class GridStrategy:
    """动态扩展网格策略"""
    name = "网格策略" # <--- 加上这一行
    def __init__(self, total_equity=10000.0, capital_rate=0.02,
                 upper=95000.0, lower=75000.0, spacing=1000.0, max_grids=60):
        self.total_equity = total_equity
        self.capital_rate = capital_rate
        self.upper = upper
        self.lower = lower
        self.spacing = spacing
        self.max_grids = max_grids
        self.active_grids = []  # 已成交的网格记录
    
    def run(self, close, total_usdt, total_btc, exchange):
        """
        每轮心跳调用一次，返回交易信号和状态信息
        :param close: 当前价格
        :param total_usdt: 账户USDT余额
        :param total_btc: 账户BTC余额
        :param exchange: OKX交易所实例 (用于真实下单)
        :return: dict 包含 signal, message, equity 等
        """
        # 1. 计算当前真实净资产
        current_equity = total_usdt + (total_btc * close)
        if total_usdt == 0 and total_btc == 0:
            current_equity = self.total_equity

        capital_per_grid = current_equity * self.capital_rate
        signal = "WAITING"
        message = ""
        executed_action = False  # 标记本轮是否执行了真实下单

        # 2. 价格跌破下限 → 开多单，下限下移
        if close < self.lower and len(self.active_grids) < self.max_grids:
            buy_volume = capital_per_grid / close
            
            # 【真实下单逻辑 - 买入】
            try:
                # 安全校验：检查 USDT 余额是否足够
                if total_usdt >= (buy_volume * close * 1.01): # 预留1%滑点空间
                    # 使用市价单确保在跌破时能立刻成交
                    order = exchange.create_market_buy_order('BTC/USDT', buy_volume)
                    message += f"✅ 真实买入 {buy_volume:.6f} BTC (ID:{order['id']}) | "
                    executed_action = True
                else:
                    message += "⚠️ USDT余额不足无法买入 | "
            except Exception as e:
                message += f"❌ 买入下单失败: {str(e)} | "

            # 更新本地策略状态
            self.active_grids.append({"price": close, "volume": buy_volume, "direction": "BUY"})
            self.lower -= self.spacing
            signal = "BUY_NET"

        # 3. 价格涨破上限 → 开多单，上限上移
        elif close > self.upper and len(self.active_grids) < self.max_grids:
            buy_volume = capital_per_grid / close
            
            # 【真实下单逻辑 - 买入】
            try:
                if total_usdt >= (buy_volume * close * 1.01):
                    order = exchange.create_market_buy_order('BTC/USDT', buy_volume)
                    message += f"✅ 真实追涨买入 {buy_volume:.6f} BTC (ID:{order['id']}) | "
                    executed_action = True
                else:
                    message += "⚠️ USDT余额不足无法追涨 | "
            except Exception as e:
                message += f"❌ 追涨下单失败: {str(e)} | "

            # 更新本地策略状态
            self.active_grids.append({"price": close, "volume": buy_volume, "direction": "BUY"})
            self.upper += self.spacing
            signal = "BUY_NET"

        # 4. 止盈检查：高于买入价5%则平仓
        grids_to_sell = []
        for i, grid in enumerate(self.active_grids):
            if grid["direction"] == "BUY" and close > grid["price"] * 1.05:
                grids_to_sell.append(i)
        
        # 执行真实卖出
        if grids_to_sell:
            total_sell_volume = sum(self.active_grids[i]["volume"] for i in grids_to_sell)
            try:
                # 安全校验：检查 BTC 余额是否足够
                if total_btc >= total_sell_volume:
                    # 使用市价单确保止盈立刻成交
                    order = exchange.create_market_sell_order('BTC/USDT', total_sell_volume)
                    message += f"💰 真实止盈卖出 {total_sell_volume:.6f} BTC (ID:{order['id']}) | "
                    executed_action = True
                else:
                    message += "⚠️ BTC余额不足无法止盈 | "
            except Exception as e:
                message += f"❌ 止盈下单失败: {str(e)} | "

            # 更新本地策略状态 (从后往前删，防止索引错乱)
            for i in reversed(grids_to_sell):
                self.active_grids.pop(i)

        # 5. 整理最终返回信息
        if not message:
            message = f"持仓{len(self.active_grids)}/{self.max_grids}格，监控中..."
        else:
            message = message.strip(" | ")

        return {
            "signal": signal,
            "message": message,
            "equity": round(current_equity, 2),
            "grids_count": len(self.active_grids),
            "upper": self.upper,
            "lower": self.lower,
        }