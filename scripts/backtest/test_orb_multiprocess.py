import os
import sys
from pathlib import Path

# 把專案根目錄加入 Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

import tx_backtest as tb

def test_orb():
    print("1. 測試載入資料...")
    df_1k, _ = tb.load_real_data(code='TXFR1')
    if df_1k.empty:
        print("警告: 資料庫中無 TXFR1 的 K 線數據！")
        return
        
    print(f"資料載入成功，共 {len(df_1k)} 筆 1K 行情數據。")
    print(f"時間範圍: {df_1k['datetime'].min()} 至 {df_1k['datetime'].max()}")
    
    print("\n2. 測試單次 ORB 回測...")
    simulator = tb.ORBBacktestSimulator(df_1k, start_capital=1000000.0, contract_type='MTX')
    metrics, trades, curve = simulator.run_strategy(
        orb_probe_minutes=15,
        orb_breakout_ticks=5,
        momentum_threshold=0.0003,
        vol_spike_ratio=1.2,
        rr_ratio=2.0,
        session_filter='both'
    )
    
    print("回測指標:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"交易次數: {len(trades)}")
    if len(trades) > 0:
        print(f"第一筆交易: {trades[0]['entry_time']} | 方向: {trades[0]['direction']} | 價格: {trades[0]['entry_price']} | 盈虧: {trades[0]['net_pnl']}")
        
    print("\n3. 測試多進程參數優化器...")
    import concurrent.futures
    import multiprocessing
    
    tasks = []
    orb_probe_range = [15, 30]
    orb_breakout_range = [5]
    momentum_range = [0.0003]
    vol_spike_range = [1.2]
    
    for probe in orb_probe_range:
        for breakout in orb_breakout_range:
            for mom in momentum_range:
                for vol in vol_spike_range:
                    tasks.append((
                        df_1k, 'MTX', 1000000.0, 0.01, 'both',
                        probe, breakout, mom, vol
                    ))
                    
    results = []
    num_workers = max(1, multiprocessing.cpu_count() - 1)
    with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(tb._orb_backtest_worker, t): t for t in tasks}
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
            
    print(f"並行測試成功！已執行 {len(results)} 組優化。")
    print("測試結果:")
    for res in results:
        print(f"  參數: {res['params']} | 交易數: {res['summary']['totalTrades']} | 勝率: {res['summary']['winRate']*100:.2f}% | 點數盈虧: {res['summary']['netPnL']}")

if __name__ == '__main__':
    test_orb()
