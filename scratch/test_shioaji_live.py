import sys
import os
import asyncio
import pandas as pd
from datetime import datetime
import logging
from pathlib import Path

# 將專案路徑加入 sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Mock Shioaji Tick 物件
class MockTick:
    def __init__(self, code: str, dt: datetime, close: float, volume: int):
        self.code = code
        self.datetime = dt
        self.close = close
        self.volume = volume

async def run_mock_live_test():
    logger.info("Initializing Shioaji real-time quote streaming Mock Test...")
    
    # 1. 導入被測試模組
    from app.main import RealTimeQuoteStreamer
    import tx_backtest as tb
    
    # 實例化 Streamer
    streamer = RealTimeQuoteStreamer()
    
    # 2. 模擬 Shioaji Client API 登入物件
    class MockShioajiApi:
        class Contracts:
            class Futures:
                # 模擬合約對象
                class Contract:
                    code = "TXFR1"
                TXFR1 = Contract()
                
        class Quote:
            def __init__(self):
                self.callback = None
                self.subscribed = []
                
            def set_on_tick_fop_v1_callback(self, cb):
                self.callback = cb
                logger.info("MockShioaji: Callback registered.")
                
            def subscribe(self, contract, quote_type):
                self.subscribed.append((contract, quote_type))
                logger.info(f"MockShioaji: Subscribed to {contract.code} {quote_type}")
                
            def unsubscribe(self, contract, quote_type):
                if (contract, quote_type) in self.subscribed:
                    self.subscribed.remove((contract, quote_type))
                logger.info(f"MockShioaji: Unsubscribed from {contract.code} {quote_type}")
                
        def __init__(self):
            self.Contracts = self.Contracts()
            self.quote = self.Quote()
            
    mock_api = MockShioajiApi()
    
    # 3. 測試啟動串流 (載入最近 1K 歷史數據與啟動協程 Worker)
    success = streamer.start_stream(mock_api)
    if not success:
        logger.error("Failed to start streamer. Preload history might have failed.")
        return
        
    logger.info("Streamer started successfully. Initial 1K buffer length: %d", len(streamer.df_1k_real))
    
    # 驗證預加載是否成功
    assert not streamer.df_1k_real.empty, "1K 歷史數據快照不應為空！"
    last_hist_dt = streamer.df_1k_real['datetime'].iloc[-1]
    last_hist_close = streamer.df_1k_real['close'].iloc[-1]
    logger.info("Last historical bar: %s | Close: %.1f", last_hist_dt, last_hist_close)
    
    # 4. 模擬實時推送 Tick 回調
    # 測試同分鐘更新 (同一分鐘內價格跳動)
    test_dt_1 = last_hist_dt
    tick_1 = MockTick(code="TXFR1", dt=test_dt_1, close=last_hist_close + 5.0, volume=10)
    logger.info(" [Test] Simulating Tick 1 (Same minute update): Price=%.1f", tick_1.close)
    streamer.on_tick(None, tick_1)
    
    # 等待協程消費 Queue
    await asyncio.sleep(0.5)
    
    # 驗證最後一筆 close 應更新為 last_hist_close + 5.0
    assert streamer.df_1k_real['close'].iloc[-1] == last_hist_close + 5.0, "同分鐘內 Tick close 更新有誤！"
    logger.info(" [Success] Same minute tick updated correctly: %.1f", streamer.df_1k_real['close'].iloc[-1])
    
    # 測試跨分鐘換棒 (進入新的一分鐘)
    test_dt_2 = last_hist_dt + pd.Timedelta(minutes=1)
    tick_2 = MockTick(code="TXFR1", dt=test_dt_2, close=last_hist_close + 12.0, volume=15)
    logger.info(" [Test] Simulating Tick 2 (Cross minute bar creation): Price=%.1f", tick_2.close)
    streamer.on_tick(None, tick_2)
    
    # 等待協程消費 Queue 并完成 1K/5K/15K/60K 聚合與 SMC 計算
    await asyncio.sleep(1.0)
    
    # 驗證是否順利多出了一根新的 K棒
    new_bar_dt = streamer.df_1k_real['datetime'].iloc[-1]
    new_bar_close = streamer.df_1k_real['close'].iloc[-1]
    logger.info("New last bar in buffer: %s | Close: %.1f", new_bar_dt, new_bar_close)
    assert new_bar_dt.minute == test_dt_2.minute, "跨分鐘換棒時間標籤有誤！"
    assert new_bar_close == last_hist_close + 12.0, "新分鐘換棒 close 價格有誤！"
    logger.info(" [Success] New bar created successfully.")
    
    # 5. 測試停止串流與釋放資源
    streamer.stop_stream()
    assert not streamer.is_active, "Streamer 應為不活躍狀態！"
    assert len(mock_api.quote.subscribed) == 0, "Shioaji 訂閱應已安全取消！"
    logger.info(" [Success] Stream stopped and resources released safely.")
    
    logger.info("🎉 All Mock Live Streaming Tests Passed!")

if __name__ == "__main__":
    asyncio.run(run_mock_live_test())
