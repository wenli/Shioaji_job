# Futures Data Downloader & SMC Backtest Pro

自動化下載 Shioaji 歷史期貨 K 棒資料，自動排程對齊聚合為多週期結構，提供磨砂玻璃 (Glassmorphic) 風格的一站式 Web 控制面盤，並整合 **SMC 多時區實時交易看盤終端**，支援高精度雙時區 (MTF) SMC 策略量化回測與二維參數最佳化掃描。

---

## 🌟 特色功能

### 1. 🗂️ 多週期支援 (Multi-Timeframes)
* 原生下載 Shioaji 最細顆粒度 **1K** 資料。
* 自動在記憶體端使用 Pandas 對齊聚合：**5K, 15K, 30K, 60K, 1D** 各週期。
* 其中 **60K (台指期 60 分K)** 採用客製化**日盤與夜盤獨立 Resample 聚合邏輯**，防止時間戳平移。

### 2. 📊 雙時區 SMC 策略量化回測 (防止未來偏差)
* **無未來偏差對齊**：5K (HTF) 大週期訊號在時間對齊時，向後 shift 一根 5K（即移至收盤時間，並採用 `merge_asof`），才傳遞給 1K (LTF) 的進場時間線，徹底消除「偷看大週期未來價格」的舞弊。
* **實盤手續費與稅金摩擦**：小台指 (MTX) 每筆雙邊收取 **NT$ 40 固定手續費**，大台指 (TX) 每筆雙邊收取 **NT$ 100 固定手續費**，並依進出場價格嚴格課徵 **0.002% 期交稅**。
* **固定比例資金風控**：動態計算交易口數，使每筆交易曝險（進場點到 1K 結構止損點的點差）嚴格限制在總資金的自訂比例（如 1%）之內。
* **主力交易策略支援**：
  * 🦄 **Unicorn Model (台指獨角獸)**：5K 流動性獵取後，1K 發生結構轉變 (CHoCH)，回測 1K 破壞塊與 FVG 共振帶進場。
  * ⚡ **Silver Bullet (台指銀色子彈)**：在日盤剛開盤的黃金 1 小時 (09:00 - 10:00) 與夜盤美股開盤前後的黃金 1 小時 (21:30 - 22:30)，捕捉高流動性下的高勝率交易機會。

### 3. ⚡ 一鍵二維參數最佳化掃描 (Unicorn Parameter Sweep)
* 後端於數秒內對獨角獸策略進行 **13 組賺賠比 (RR: 1.2 ~ 3.6)** 與 **6 組最小止損限制 (Min SL: 15 ~ 40 點)** 的二維參數掃描，並於前端以發光高亮的互動式熱力矩陣圖直觀呈現策略的績效高原。

### 4. 📌 續流續傳與背景排程
* 智慧查詢表格「最後一筆 `ts`」時間戳，僅抓取補足後續新生成的資料。
* 整合 `APScheduler` 背景排程同步。

### 5. 🦄 SMC MTF 實時交易看盤終端 (SMC MTF Live Trading Terminal) [NEW]
* **2x2 四宮格 TradingView 互動畫布**：Web 前端採用輕量圖表 `lightweight-charts` 同步呈現 1K, 5K, 15K, 60K 實時 K棒生長。
* **Shioaji 實時串流行情 (Real-time Live Stream)**：工業級 `asyncio.Queue` 執行緒安全消費，實時與 SQLite 拼接歷史並重聚合，秒級廣播 2x2 圖表與動態 SMC 指標（OB 水平帶、Sweep 標記、CHoCH 虛線）。
* **2 天歷史數據預載與 SMC 同步渲染**：圖表初次連線瞬間預載 2500 根 1K 歷史數據（約為 2 天完整交易日），並同步渲染歷史上的 OB/FVG 霓虹通道帶。
* **全球台北時區 Asia/Taipei (UTC+8) 強強對齊**：雙層時區格式化強護甲，徹底阻絕海外瀏覽器時區偏移，不論身處何處看盤時間軸皆 100% 台北時間。
* **日/夜盤台北時段精準識別**：動態識別 Tick 行情日夜盤時段，警報牆帶有 `[日盤]` (天藍) / `[夜盤]` (霓虹紫) 的亮麗標籤。

---

## ⚙️ 安裝環境與配置

### 1. 安裝 Dependencies
```bash
pip install -r requirements.txt
```

### 2. 環境變數設定 (`.env`)
於專案根目錄下建立 `.env` 檔案，配置您的 Shioaji 憑證與追蹤合約：

```env
SHIOAJI_API_KEY="你的_shioaji_api_key"
SHIOAJI_SECRET_KEY="你的_shioaji_secret_key"
SHIOAJI_SIMULATION="True"  # True 為模擬模式

# 操作目標合約 code 設置 (預設為 TXFR1 近月連續)
TARGET_CODE="TXFR1"

# 若資料庫全空，預設載入起始歷史點
DEFAULT_START_DATE="2025-01-01"

DB_NAME="Shioaji.db"
```

---

## 🚀 啟動方式

確保 `.env` 配置妥善後，直接執行主入口腳本：

```bash
python app/main.py
```

* **量化回測與控制面盤**: `http://127.0.0.1:8000/`
* **SMC 實時看盤交易終端**: `http://127.0.0.1:8000/live`

---

## 📁 專案架構概覽

* `app/main.py` - FastAPI 服務入口，設定靜態檔案伺服、數據狀態、手動同步，以及**實時看盤 WebSocket 雙模廣播推送與重播引擎**。
* `tx_backtest.py` - **量化回測與指標核心模組**：SMC 雙時區訊號引擎 (`TaiwanFuturesSMCEngine`)、回測模擬器。
* `download_futures_data.py` - 核心數據下載層：SQL 表格初始化、斷點補齊、Pandas Resample 聚合對齊。
* `scheduler_manager.py` - 背景調度中心，管理 `APScheduler` 例行 cron 同步任務。
* `frontend/dashboard.html` - 回測一體化前端面盤。
* `frontend/live_terminal.html` - **SMC 實時看盤終端網頁**：磨砂玻璃暗黑霓虹風設計，整合 2x2 lightweight-charts 與 SMC 實時通知警報牆。
* `scratch/test_shioaji_live.py` - Shioaji 即時行情串流 Mock 測試腳本。
* `scratch/test_history_init.py` - 預載數據與指標 NaN 清洗 Mock 測試腳本。
* `Shioaji.db` - SQLite 資料庫（儲存高精度 TXFR1 數據）。

---

## 🔗 API 與 WebSocket 端點參考

| 方法 | 端點 | 說明 |
| :--- | :--- | :--- |
| `GET` | `/` | 渲染 Web 一體化回測 Dashboard UI |
| `GET` | `/live` | 渲染 SMC 實時看盤交易終端網頁 UI |
| `GET` | `/api/status` | 讀取 SQLite 整合狀況與 Shioaji 通訊狀況 |
| `POST` | `/api/sync` | 背景手動異步觸發 Full-Sync 下載同步 |
| `POST` | `/api/backtest` | 執行台指期雙時區 SMC 策略量化回測 |
| `POST` | `/api/backtest/optimize` | 執行獨角獸策略的二維參數優化 |
| `WS` | `/api/live/ws` | **實時看盤 WebSocket 管道**，支援行情重播、模擬實時與 Shioaji 真實實盤行情廣播 |
| `GET` | `/api/logs` | 讀取日誌端點 |

---
