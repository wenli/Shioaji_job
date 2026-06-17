import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import tx_backtest as tb

print("1. 載入台指期歷史 1K 行情數據...")
try:
    df_1k, _ = tb.load_real_data(code='TXFR1', start_date='2026-05-01', end_date='2026-06-01')
    print(f"成功載入 1K 數據，共 {len(df_1k)} 筆。")
except Exception as e:
    print(f"載入數據失敗: {e}")
    sys.exit(1)

# 2. 測試 4 種停損模式
sl_modes = ['bar_extreme', 'range_edge', 'atr_dynamic', 'fixed_points']

for mode in sl_modes:
    print(f"\n--- 測試停損模式: {mode} ---")
    simulator = tb.ORBBacktestSimulator(df_1k, start_capital=1000000.0, contract_type='MTX')
    simulator.risk_pct = 0.01
    
    metrics, trades, curve = simulator.run_strategy(
        orb_probe_minutes=15,
        orb_breakout_ticks=5,
        momentum_threshold=0.0003,
        vol_spike_ratio=1.2,
        rr_ratio=2.0,
        session_filter='both',
        orb_atr_period=14,
        orb_atr_multiplier=2.0,
        force_min_lot=True,
        sl_mode=mode,
        min_sl_points=20.0,
        fixed_sl_points=30.0
    )
    
    print(f"回測完成！交易次數: {metrics['total_trades']}, 勝率: {metrics['win_rate']}%, 獲利因子: {metrics['profit_factor']}, 淨盈虧: {metrics['net_profit']}")
    if len(trades) > 0:
        print(f"首筆交易細節:")
        t = trades[0]
        print(f"  方向: {t['direction']}, 進場價: {t['entry_price']}, 出場價: {t['exit_price']}, 停損: {t['stop_loss']}, 停利: {t['take_profit']}, 原因: {t['reason']}, 停損模式: {t['entry_indicators'].get('sl_mode')}")

print("\n3. 測試參數優化器 (限制數據範圍跑小量優化，防止時間過長) ...")
# 為了測試優化器，我們使用前 500 筆數據，這將非常迅速地跑完 1280 種組合
df_1k_small = df_1k.iloc[:500].copy()
try:
    results, json_path, md_path = tb.run_orb_parameter_optimization(
        df_1k_small,
        contract_type='MTX',
        start_capital=1000000.0,
        risk_pct=0.01,
        session_filter='both',
        force_min_lot=True
    )
    print("參數優化測試成功！")
    print(f"報告輸出路徑 (JSON): {json_path}")
    print(f"報告輸出路徑 (Markdown): {md_path}")
except Exception as e:
    print(f"參數優化測試失敗: {e}")
