# -*- coding: utf-8 -*-
"""
台指期 SMC 銀色子彈 (Silver Bullet) 多進程網格參數優化器
使用 multiprocessing 平行化評估不同濾網門檻、掛單層級與風控參數組合。
"""

import os
import sys
import itertools
import multiprocessing
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional
import pandas as pd
import numpy as np

# 加入專案路徑
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.strategy.silver_bullet import SilverBulletConfig, SilverBulletEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data


def evaluate_single_param(args: Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]) -> Dict[str, Any]:
    """
    單一參數組合評估任務 (Top-level 函式供 multiprocessing pickle)
    """
    df_1k, df_5k, p = args
    cfg = SilverBulletConfig(
        contract_type=p.get("contract_type", "MTX"),
        enable_vwap=p.get("enable_vwap", True),
        enable_volume_spike=p.get("enable_volume_spike", True),
        vol_spike_ratio=p.get("vol_spike_ratio", 1.2),
        entry_mode=p.get("entry_mode", "limit_retest"),
        fvg_entry_level=p.get("fvg_entry_level", "ce_50"),
        max_wait_bars=p.get("max_wait_bars", 5),
        sl_mode=p.get("sl_mode", "swing_extreme"),
        enable_breakeven=p.get("enable_breakeven", True),
        be_trigger_r=p.get("be_trigger_r", 1.0),
        tp_mode=p.get("tp_mode", "opposite_swing"),
        rr_ratio=p.get("rr_ratio", 2.0),
        enable_dst_detection=True
    )

    engine = SilverBulletEngine(df_1k, df_5k, cfg)
    engine.df = df_1k  # 直接使用已預先計算好指標的 DataFrame，大幅加速網格搜尋
    summary, trades, eq_curve = engine.run_backtest()

    # 綜合評分：以獲利因子與淨損益綜合考量，要求有一定交易樣本數
    mdd = max(0.01, summary['max_drawdown_pct'])
    calmar = summary['total_return_pct'] / mdd if summary['total_return_pct'] > 0 else (summary['total_return_pct'] * mdd)
    
    res = {
        **p,
        "total_trades": summary['total_trades'],
        "win_rate": summary['win_rate'],
        "profit_factor": summary['profit_factor'],
        "max_drawdown_pct": summary['max_drawdown_pct'],
        "net_profit": summary['net_profit'],
        "total_return_pct": summary['total_return_pct'],
        "be_trades": summary['be_trades'],
        "filtered_count": summary['filtered_count'],
        "calmar_ratio": round(calmar, 2)
    }
    return res


def run_grid_optimization(
    df_1k: pd.DataFrame,
    df_5k: pd.DataFrame,
    param_grid: Dict[str, List[Any]],
    contract_type: str = "MTX",
    n_workers: int = 0
) -> pd.DataFrame:
    """執行網格優化搜尋"""
    if n_workers <= 0:
        n_workers = max(1, multiprocessing.cpu_count() - 1)

    # 展開所有參數組合
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = [dict(zip(keys, prod)) for prod in itertools.product(*values)]
    for c in combinations:
        c["contract_type"] = contract_type

    total_tasks = len(combinations)
    print(f"\n[網格優化啟動] 共展開 {total_tasks:,} 組參數，使用 {n_workers} 個平行工作進程...")

    # 預先計算好特徵 DataFrame，減少重複運算
    print("正在預先計算 5K/1K 數據特徵...")
    dummy_engine = SilverBulletEngine(df_1k, df_5k, SilverBulletConfig(contract_type=contract_type))
    prepared_df = dummy_engine.prepare_data()

    # 打包任務引數
    task_args = [(prepared_df, df_5k, p) for p in combinations]

    # 多進程執行
    results = []
    with multiprocessing.Pool(processes=n_workers) as pool:
        for idx, res in enumerate(pool.imap_unordered(evaluate_single_param, task_args, chunksize=5), 1):
            results.append(res)
            if idx % max(1, total_tasks // 10) == 0 or idx == total_tasks:
                print(f"進度: {idx:,} / {total_tasks:,} ({idx / total_tasks * 100:.1f}%) 完成")

    res_df = pd.DataFrame(results)

    # 排序：優先考慮淨損益與獲利因子
    res_df = res_df.sort_values(by=["net_profit", "profit_factor", "win_rate"], ascending=False).reset_index(drop=True)

    # 輸出目錄
    output_dir = os.path.join(os.path.dirname(__file__), "../../reports")
    os.makedirs(output_dir, exist_ok=True)
    csv_file = os.path.join(output_dir, f"silver_bullet_grid_{contract_type.lower()}.csv")
    res_df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"\n[網格搜尋結果已匯出 CSV]: {os.path.abspath(csv_file)}")

    # 終端印出前 10 名
    print_top_results(res_df, top_n=10)
    return res_df


def print_top_results(df: pd.DataFrame, top_n: int = 10):
    """印出最優參數排名前 N 名"""
    print("\n" + "=" * 95)
    print(f"{'排名':<4} | {'量能門檻':<8} | {'掛單層級':<8} | {'等待根數':<8} | {'停損模式':<13} | {'移保本 R':<8} | {'交易數':<6} | {'勝率':<8} | {'PF':<6} | {'淨損益 (NT$)':<12}")
    print("-" * 95)

    for i in range(min(top_n, len(df))):
        r = df.iloc[i]
        rank = f"#{i+1}"
        vol = f"{r.get('vol_spike_ratio', 1.0)}x"
        lvl = str(r.get('fvg_entry_level', 'ce_50'))
        wait = f"{r.get('max_wait_bars', 5)}根"
        sl = str(r.get('sl_mode', 'swing_extreme'))
        be = f"{r.get('be_trigger_r', 1.0)}R" if r.get('enable_breakeven', True) else "OFF"
        trades = f"{r['total_trades']}次"
        wr = f"{r['win_rate']:.1f}%"
        pf = f"{r['profit_factor']:.2f}"
        pnl = f"NT$ {r['net_profit']:+,.0f}"

        print(f"{rank:<4} | {vol:<8} | {lvl:<8} | {wait:<8} | {sl:<13} | {be:<8} | {trades:<6} | {wr:<8} | {pf:<6} | {pnl:<12}")
    print("=" * 95)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="台指期 SMC 銀色子彈多進程網格優化器")
    parser.add_argument("--code", type=str, default="TXFR1")
    parser.add_argument("--contract", type=str, default="MTX", choices=["MTX", "TX"])
    parser.add_argument("--start", type=str, default="2026-04-01")
    parser.add_argument("--end", type=str, default="2026-07-01")
    parser.add_argument("--workers", type=int, default=0)

    args = parser.parse_args()
    db_path = get_db_path()

    df_1k, df_5k = load_historical_data(db_path, code=args.code, start_date=args.start, end_date=args.end)

    # 精選關鍵調參網格
    grid = {
        "vol_spike_ratio": [1.0, 1.15, 1.3],
        "fvg_entry_level": ["ce_50", "edge"],
        "max_wait_bars": [3, 5, 8],
        "sl_mode": ["swing_extreme", "bar_extreme"],
        "enable_breakeven": [True, False],
        "be_trigger_r": [0.8, 1.0, 1.2],
        "rr_ratio": [1.5, 2.0]
    }

    run_grid_optimization(df_1k, df_5k, grid, contract_type=args.contract, n_workers=args.workers)
