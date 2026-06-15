# -*- coding: utf-8 -*-
"""
測試後端實時 ORB 計算邏輯
驗證當前開盤區間確立、突破偵測、動能門檻與量增比率過濾。
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import tx_backtest as tb

def generate_mock_1k_data(start_time_str, num_bars, base_price=20000.0, trend=0.0):
    """
    產生模擬的 1K K線 DataFrame。
    """
    dt_start = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
    data = []
    
    current_price = base_price
    
    # 決定是日盤還是夜盤以標註 session
    def get_session(dt):
        t = dt.time()
        if (t >= datetime.strptime("08:45", "%H:%M").time()) and (t <= datetime.strptime("13:45", "%H:%M").time()):
            return 'day'
        else:
            return 'night'

    for i in range(num_bars):
        dt = dt_start + timedelta(minutes=i)
        
        # 模擬隨機波動，加上趨勢
        open_p = current_price
        close_p = open_p + np.random.normal(0, 10) + trend
        high_p = max(open_p, close_p) + np.random.uniform(0, 8)
        low_p = min(open_p, close_p) - np.random.uniform(0, 8)
        volume = int(np.random.uniform(50, 150))
        
        data.append({
            'datetime': dt,
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p,
            'volume': volume,
            'session': get_session(dt)
        })
        current_price = close_p
        
    df = pd.DataFrame(data)
    return df

def test_orb_calculation():
    print("========== 測試 1: 驗證開盤區間確立前 (未滿15分鐘) ==========")
    # 日盤開盤: 08:45:00，產生 10 根 K線 (到 08:54)
    df_early = generate_mock_1k_data("2026-06-15 08:45:00", 10, base_price=20000.0)
    
    orb_params = {
        "orb_probe_minutes": 15,
        "orb_breakout_ticks": 5,
        "momentum_threshold": 0.0003,
        "vol_spike_ratio": 1.2
    }
    
    status_early = tb.calculate_realtime_orb_status(df_early, **orb_params)
    print(f"確立狀態 (預期 False): {status_early['is_established']}")
    print(f"確立倒數 (秒): {status_early['countdown_seconds']} 秒")
    print(f"當前高點線 (包含 ticks 緩衝): {status_early['high_line']}")
    print(f"當前低點線 (包含 ticks 緩衝): {status_early['low_line']}")
    
    assert not status_early['is_established'], "應該還沒有確立"
    assert status_early['countdown_seconds'] > 0, "倒數秒數應該大於零"
    assert status_early['high_line'] > 0, "高點線應該大於零"
    
    print("\n========== 測試 2: 驗證開盤區間確立後 (已滿15分鐘) ==========")
    # 產生 20 根 K線 (到 09:04)
    df_established = generate_mock_1k_data("2026-06-15 08:45:00", 20, base_price=20000.0)
    
    # 為了方便測試，我們把前 15 分鐘的 high / low 記下來
    probe_end = datetime.strptime("2026-06-15 08:45:00", "%Y-%m-%d %H:%M:%S") + timedelta(minutes=15)
    df_probe = df_established[df_established['datetime'] <= probe_end]
    expected_high = df_probe['high'].max() + orb_params['orb_breakout_ticks']
    expected_low = df_probe['low'].min() - orb_params['orb_breakout_ticks']
    
    status_est = tb.calculate_realtime_orb_status(df_established, **orb_params)
    print(f"確立狀態 (預期 True): {status_est['is_established']}")
    print(f"確立倒數 (秒，預期 0): {status_est['countdown_seconds']}")
    print(f"實際高點線: {status_est['high_line']} | 預期高點線: {expected_high}")
    print(f"實際低點線: {status_est['low_line']} | 預期低點線: {expected_low}")
    
    assert status_est['is_established'], "應該已經確立"
    assert status_est['countdown_seconds'] == 0, "倒數秒數應該為 0"
    assert abs(status_est['high_line'] - expected_high) < 1e-5, "高點線不符預期"
    
    print("\n========== 測試 3: 驗證多頭突破與量增/動能過濾 ==========")
    # 我們複製一個確立後的 df，在最後一根 K線人為製造一個「多頭突破 + 動能達標 + 爆量」的訊號
    df_breakout = df_established.copy()
    last_idx = len(df_breakout) - 1
    
    # 前 5 根平均 volume
    prev_vols = df_breakout.loc[last_idx-5:last_idx-1, 'volume'].values
    avg_vol = np.mean(prev_vols)
    
    # 設定最後一根：價格高於 expected_high (即高點 + breakout_ticks)，並且 volume 達標，動能達標
    breakout_price = expected_high + 10.0
    df_breakout.loc[last_idx, 'open'] = expected_high - 2.0
    df_breakout.loc[last_idx, 'close'] = breakout_price
    df_breakout.loc[last_idx, 'high'] = breakout_price + 2.0
    df_breakout.loc[last_idx, 'volume'] = int(avg_vol * orb_params['vol_spike_ratio'] * 1.5)
    
    status_bo = tb.calculate_realtime_orb_status(df_breakout, **orb_params)
    print(f"突破狀態 (預期 1，代表多頭突破): {status_bo['breakout_status']}")
    print(f"當前 Volume: {df_breakout.loc[last_idx, 'volume']} | 5日均量: {status_bo['vol_avg_5']} (預期量比大於 {orb_params['vol_spike_ratio']})")
    
    assert status_bo['breakout_status'] == 1, "應該偵測到多頭突破"
    
    print("\n========== 測試 4: 驗證歷史區間全量計算 (get_historical_orb_ranges) ==========")
    df_hist = generate_mock_1k_data("2026-06-15 08:45:00", 60, base_price=20000.0)
    # 加上一個夜盤
    df_night = generate_mock_1k_data("2026-06-15 15:00:00", 60, base_price=20010.0)
    df_total = pd.concat([df_hist, df_night]).reset_index(drop=True)
    
    hist_ranges = tb.get_historical_orb_ranges(df_total, orb_probe_minutes=15, orb_breakout_ticks=5)
    print("產出的歷史區間交易時段起點:")
    for sess, info in hist_ranges.items():
        print(f" - 時段: {sess} | High: {info['high']} | Low: {info['low']} | 確立: {info['is_established']}")
        
    assert len(hist_ranges) >= 2, "歷史區間至少要有 2 個時段"
    
    print("\n[SUCCESS] 所有後端 ORB 實時計算單元測試順利通過！")

if __name__ == "__main__":
    test_orb_calculation()
