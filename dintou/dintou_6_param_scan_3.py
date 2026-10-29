#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dintou_6_param_scan.py — 空对冲参数扫描器
用法: python dintou_6_param_scan.py
说明: 从dintou_6.V2.0.py导入回测函数，扫描不同参数组合，输出持币量排名
"""

import sys
import os
import pandas as pd
import numpy as np

def backtest_short_hedge_scan(df, params):
    """空对冲回测（扫描版）"""
    cash = params.get('initial_cash', 10000.0)
    dca = params.get('dca_per_buy', 10.0)
    ratio1 = params.get('ratio1', 0.25)
    ratio2 = params.get('ratio2', 0.60)
    ratio3 = params.get('ratio3', 1.20)
    thr1 = params.get('thr1', 1.15)
    thr2 = params.get('thr2', 1.35)
    thr3 = params.get('thr3', 1.60)
    sell_ratio = params.get('sell_ratio', 0.03)
    profit_thr = params.get('profit_thr', 1.80)
    
    spot_coin = 0.0
    spot_avg = 0.0
    total_cost = 0.0
    short_coin = 0.0
    short_avg = 0.0
    short_margin = 0.0
    equity_curve = []

    for i, row in df.iterrows():
        price = float(row["close"])
        # 定投买入
        if cash >= dca and price > 0:
            qty = dca / price
            total_cost += dca
            new_total = spot_avg * spot_coin + dca
            spot_coin += qty
            spot_avg = new_total / spot_coin if spot_coin > 0 else 0
            cash -= dca

        # 止盈平仓 - 币本位空单止盈：平仓赚DOGE回到现货
        # 修复：原来这里卖的是现货币，改为平仓空单赚币
        if spot_coin > 0 and short_coin > 0 and short_avg > 0:
            profit_ratio = short_avg / price if price > 0 else 0
            if profit_ratio >= profit_thr:
                # sell_ratio 控制止盈平仓比例（0.03=平3%空单）
                close_qty = short_coin * sell_ratio
                if close_qty > 0 and close_qty <= short_coin:
                    # 币本位空单盈亏（DOGE形式）
                    if short_avg > price:
                        pnl_coins = close_qty * (short_avg - price) / short_avg
                    else:
                        pnl_coins = 0.0
                    # 返还保证金 + 利润到现货（赚的是DOGE！）
                    spot_coin += close_qty + pnl_coins
                    # 更新空单
                    short_coin -= close_qty
                    short_margin -= close_qty
                    if short_coin <= 1e-9:
                        short_coin = 0.0
                        short_avg = 0.0
                        short_margin = 0.0

        # 开空逻辑 - 币本位（DOGE做保证金）
        # 修复：原来用cash(USDT)做保证金，改为spot_coin(DOGE)做保证金
        if spot_coin > 0 and spot_avg > 0 and price > 0:
            ratio = price / spot_avg
            target_ratio = 0
            if ratio >= thr3:
                target_ratio = ratio3
            elif ratio >= thr2:
                target_ratio = ratio2
            elif ratio >= thr1:
                target_ratio = ratio1
            if target_ratio > 0:
                target_short = spot_coin * target_ratio
                if target_short > short_coin:
                    add_qty = target_short - short_coin
                    # 币本位：保证金用DOGE（从现货扣）
                    if spot_coin >= add_qty:
                        spot_coin -= add_qty          # 扣除保证金DOGE
                        short_margin += add_qty        # 记录保证金
                        short_coin += add_qty          # 增加空单数量
                        # 更新均价
                        if short_coin > 0:
                            if short_avg > 0:
                                short_avg = (short_avg * (short_coin - add_qty) + price * add_qty) / short_coin
                            else:
                                short_avg = price

        # 平空逻辑 - 价格回到均价附近强制平仓（止损/均值回归）
        # 修复：币本位盈亏以DOGE结算，回到现货
        if short_coin > 0 and short_avg > 0 and price > 0:
            ratio = price / spot_avg if spot_avg > 0 else 999
            if ratio <= 1.0:
                # 全部平仓
                if short_avg > price:
                    pnl_coins = short_coin * (short_avg - price) / short_avg
                else:
                    pnl_coins = -short_coin * (price - short_avg) / short_avg
                # 返还保证金 + 盈亏到现货（DOGE结算）
                spot_coin += short_margin + pnl_coins
                short_coin = 0.0
                short_avg = 0.0
                short_margin = 0.0

        equity = cash + spot_coin * price + short_margin * price + (short_avg - price) * short_coin
        equity_curve.append(equity)

    final_price = float(df.iloc[-1]["close"])
    final_equity = cash + spot_coin * final_price + short_margin * final_price + (short_avg - final_price) * short_coin
    max_dd = 0.0
    peak = equity_curve[0]
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        'spot_coin': spot_coin,
        'short_coin': short_coin,
        'cash': cash,
        'final_equity': final_equity,
        'return_pct': (final_equity - params.get('initial_cash', 10000)) / params.get('initial_cash', 10000) * 100,
        'max_dd': max_dd * 100,
        'final_price': final_price,
    }


def scan_parameters(df, param_grid):
    """参数扫描"""
    results = []
    total = len(param_grid)
    for idx, params in enumerate(param_grid):
        try:
            r = backtest_short_hedge_scan(df, params)
            row = {**params, **r}
            results.append(row)
            if (idx + 1) % 20 == 0:
                print(f"  已跑 {idx+1}/{total} 组合...")
        except Exception as e:
            print(f"  组合 {params} 出错: {e}")
            continue
    
    df_result = pd.DataFrame(results)
    df_result = df_result.sort_values('spot_coin', ascending=False)
    return df_result


if __name__ == "__main__":
    import yfinance as yf
    
    print("=" * 80)
    print("DOGE 空对冲参数扫描器")
    print("=" * 80)
    
    print("\n正在获取DOGE数据...")
    ticker = yf.Ticker("DOGE-USD")
    df = ticker.history(start="2021-01-01", end="2026-10-23")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    print(f"数据量: {len(df)} 条, 范围 {df.index[0]} ~ {df.index[-1]}")
    
    print("\n定义参数扫描范围...")
    param_grid = []
    
    # 基础组合: 默认参数
    base_params = {
        'initial_cash': 10000.0, 'dca_per_buy': 10.0,
        'ratio1': 0.25, 'ratio2': 0.60, 'ratio3': 1.20,
        'thr1': 1.15, 'thr2': 1.35, 'thr3': 1.60,
        'sell_ratio': 0.03, 'profit_thr': 1.80,
    }
    param_grid.append(base_params)
    
    # 激进赚币组1
    param_grid.append({
        'initial_cash': 10000.0, 'dca_per_buy': 10.0,
        'ratio1': 0.35, 'ratio2': 0.80, 'ratio3': 1.50,
        'thr1': 1.08, 'thr2': 1.25, 'thr3': 1.50,
        'sell_ratio': 0.01, 'profit_thr': 1.50,
    })
    
    # 激进赚币组2
    param_grid.append({
        'initial_cash': 10000.0, 'dca_per_buy': 10.0,
        'ratio1': 0.40, 'ratio2': 0.90, 'ratio3': 1.60,
        'thr1': 1.05, 'thr2': 1.20, 'thr3': 1.45,
        'sell_ratio': 0.01, 'profit_thr': 1.40,
    })
    
    # 保守赚币组
    param_grid.append({
        'initial_cash': 10000.0, 'dca_per_buy': 10.0,
        'ratio1': 0.30, 'ratio2': 0.70, 'ratio3': 1.30,
        'thr1': 1.10, 'thr2': 1.30, 'thr3': 1.55,
        'sell_ratio': 0.02, 'profit_thr': 1.60,
    })
    
    # 网格扫描: 单独测试每个参数
    for thr1_val in [1.05, 1.08, 1.10, 1.12, 1.15, 1.20]:
        p = base_params.copy()
        p['thr1'] = thr1_val
        param_grid.append(p)
    
    for r1_val in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
        p = base_params.copy()
        p['ratio1'] = r1_val
        param_grid.append(p)
    
    for sr_val in [0.01, 0.02, 0.03, 0.05, 0.10]:
        p = base_params.copy()
        p['sell_ratio'] = sr_val
        param_grid.append(p)
    
    for pt_val in [1.30, 1.50, 1.60, 1.80, 2.00]:
        p = base_params.copy()
        p['profit_thr'] = pt_val
        param_grid.append(p)
    
    for r2_val in [0.40, 0.50, 0.60, 0.70, 0.80]:
        p = base_params.copy()
        p['ratio2'] = r2_val
        param_grid.append(p)
    
    # 去重
    unique_params = []
    seen = set()
    for p in param_grid:
        key = (p['thr1'], p['thr2'], p['thr3'], p['ratio1'], p['ratio2'], p['ratio3'], p['sell_ratio'], p['profit_thr'])
        if key not in seen:
            seen.add(key)
            unique_params.append(p)
    print(f"总共 {len(unique_params)} 个独立组合")
    
    print("\n" + "=" * 80)
    print("开始参数扫描...")
    print("=" * 80)
    
    df_result = scan_parameters(df, unique_params)
    
    print("\n" + "=" * 120)
    print("参数扫描结果 (按最终持币量降序排列):")
    print("=" * 120)
    
    cols = ['thr1', 'thr2', 'thr3', 'ratio1', 'ratio2', 'ratio3', 'sell_ratio', 'profit_thr', 
            'spot_coin', 'short_coin', 'cash', 'final_equity', 'return_pct', 'max_dd']
    display_cols = [c for c in cols if c in df_result.columns]
    print(df_result[display_cols].head(20).to_string(index=False))
    
    output_path = "param_scan_results.csv"
    df_result.to_csv(output_path, index=False)
    print(f"\n完整结果已保存到: {output_path}")
    
    print("\n" + "=" * 80)
    print("参数调整建议:")
    print("=" * 80)
    print("""
1. 想多赚币 -> 降低 thr1/thr2/thr3 (更早开空) + 提高 ratio1/ratio2/ratio3 (更大空单)
   效果: 更多空单循环 = 更多币本位利润
   
2. 想手里币更多 -> 降低 sell_ratio (止盈时少卖现货)
   效果: 每次止盈卖出的币更少 = 最终持仓币更多
   
3. 想更频繁止盈 -> 降低 profit_thr (更早锁定利润)
   效果: 利润落袋更快 = 可以更快开启下一轮空单循环
   
4. 风险提示:
   - 阈值太低 -> 正常波动就触发开空 -> 可能被反复止损
   - 比例太高 -> 保证金不够 -> 错过定投买入
   - sell_ratio 太低 -> 现货卖太少 -> USDT余额偏低
   
建议先用上面扫描结果中排名靠前的参数跑完整回测验证!
""")
