import sys
import os
import asyncio
import pandas as pd
import sqlite3
import math
import logging
from pathlib import Path

# 將專案路徑加入 sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

async def run_history_init_test():
    logger.info("Initializing preloaded history_init payload Mock Test...")
    
    # 1. 載入我們的後端主函數
    from app.main import get_history_init_payload
    
    # 2. 從 SQLite 加載 150 根 1K 歷史數據
    db_path = "Shioaji.db"
    if not os.path.exists(db_path):
        db_path = r"C:\Intel\TW_Stock_K-Line_Chart\SK.db"
        
    if not os.path.exists(db_path):
        logger.error(f"Database file not found: {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    query = "SELECT ts, Open as open, High as high, Low as low, Close as close, Volume as volume FROM futures1k WHERE code='TXFR1' ORDER BY ts DESC LIMIT 2500;"
    df_hist = pd.read_sql_query(query, conn)
    conn.close()
    
    if df_hist.empty:
        logger.error("No historical data found in database. Cannot run test.")
        return
        
    # 按時間正序
    df_hist = df_hist.iloc[::-1].copy().reset_index(drop=True)
    df_hist['datetime'] = pd.to_datetime(df_hist['ts'])
    df_hist = df_hist.drop(columns=['ts'])
    
    logger.info(f"Loaded {len(df_hist)} 1K bars from database. Splicing start: {df_hist['datetime'].iloc[0]} | end: {df_hist['datetime'].iloc[-1]}")
    
    # 3. 調用 get_history_init_payload 進行歷史計算與對齊
    payload = await get_history_init_payload(df_hist)
    
    # 4. 驗證 payload 的完整性
    assert payload, "生成的 payload 不應為空！"
    assert payload["type"] == "history_init", "封包 type 應為 history_init！"
    assert "data" in payload, "封包應包含 data 屬性！"
    
    data = payload["data"]
    assert "1k" in data, "data 應包含 1k 數據！"
    assert "5k" in data, "data 應包含 5k 數據！"
    assert "15k" in data, "data 應包含 15k 數據！"
    assert "60k" in data, "data 應包含 60k 數據！"
    
    logger.info("Payload generated successfully.")
    logger.info(" --- Timeframe Length Validation --- ")
    logger.info(" 1K History bars: %d", len(data["1k"]))
    logger.info(" 5K History bars: %d", len(data["5k"]))
    logger.info(" 15K History bars: %d", len(data["15k"]))
    logger.info(" 60K History bars: %d", len(data["60k"]))
    
    assert len(data["1k"]) == 2500, "1K 歷史數據長度應為 2500！"
    assert len(data["5k"]) > 0, "5K 歷史數據不應為空！"
    assert len(data["15k"]) > 0, "15K 歷史數據不應為空！"
    assert len(data["60k"]) > 0, "60K 歷史數據不應為空！"
    
    # 5. 遞迴檢測 JSON 中是否存在非法 NaN 值
    def scan_for_nan(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                scan_for_nan(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                scan_for_nan(item, f"{path}[{idx}]")
        elif isinstance(obj, float):
            assert not math.isnan(obj), f"發現未清洗的 NaN 值於：{path}"
            assert not math.isinf(obj), f"發現未清洗的 Inf 值於：{path}"
            
    scan_for_nan(payload)
    logger.info(" [Success] Checked all floating values. No NaN / Inf found in payload!")
    
    # 6. 驗證最後一筆 SMC 的 indicators 欄位
    last_1k_smc = data["1k"][-1]["smc"]
    logger.info(" --- Last 1K SMC Indicators Sample ---")
    for k, v in last_1k_smc.items():
        logger.info("  %s: %s", k, v)
        
    logger.info("🎉 Preloaded history_init payload Mock Test Passed 100%!")

if __name__ == "__main__":
    asyncio.run(run_history_init_test())
