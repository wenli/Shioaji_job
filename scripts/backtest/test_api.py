import urllib.request
import json

def test_backtest_api():
    url = "http://127.0.0.1:8000/api/backtest/orb"
    data = {
        "start_date": "2026-05-01",
        "end_date": "2026-05-05",
        "start_capital": 1000000.0,
        "risk_pct": 0.01,
        "contract_type": "MTX",
        "session_filter": "both",
        "orb_probe_minutes": 15,
        "orb_breakout_ticks": 5,
        "momentum_threshold": 0.0003,
        "vol_spike_ratio": 1.2,
        "rr_ratio": 2.0
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            print("1. /api/backtest/orb 測試成功！")
            print("  回測結果: Success =", res_data.get('success'))
            print("  交易次數:", len(res_data.get('trades', [])))
            print("  權益曲線點數:", len(res_data.get('curve', [])))
    except Exception as e:
        print("1. /api/backtest/orb 測試失敗:", e)

def test_optimize_api():
    url = "http://127.0.0.1:8000/api/backtest/orb/optimize"
    data = {
        "start_date": "2026-05-01",
        "end_date": "2026-05-05",
        "start_capital": 1000000.0,
        "risk_pct": 0.01,
        "contract_type": "MTX",
        "session_filter": "both"
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            print("\n2. /api/backtest/orb/optimize 測試成功！")
            print("  回測結果: Success =", res_data.get('success'))
            best = res_data.get('best_combination', {})
            print("  最佳組合:", best.get('params') if best else "無合格組合")
            print("  優化結果總數:", len(res_data.get('opt_results', [])))
            print("  報告路徑: Markdown =", res_data.get('report_paths', {}).get('markdown'))
    except Exception as e:
        print("\n2. /api/backtest/orb/optimize 測試失敗:", e)

if __name__ == '__main__':
    test_backtest_api()
    test_optimize_api()
