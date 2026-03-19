# Futures Data Downloader

自動化下載 Shioaji 歷史期貨 K 棒資料，自動排程對齊聚合為多週期結構，並提供現代化 Glassmorphic Web Dashboard 營運管理的工具系統。

---

## 🌟 特色功能

1.  **🗂️ 多週期支援 (Multi-Timeframes)**
    *   原生下載 Shioaji 最細顆粒度 **1K** 資料。
    *   自動在記憶體端使用 Pandas 對齊聚合：**5K, 15K, 30K, 60K, 1D** 各週期。
2.  **📌 續流續傳 (Breakpoint Resume)**
    *   智慧查詢各個表格的「最後一筆 `ts`」時間戳，重啟或例行同步時，僅抓取補足後續新生成的資料，免重複下載。
3.  **⏲️ 背景任務自動排程 (Scheduler)**
    *   自動整合 `APScheduler` 並於背景在每分鐘的第 5 秒進行掃描捕抓。
4.  **📅 標準 TIMESTAMP 日期格式**
    *   SQLite 中的 `ts` 直接儲存為標準化 `YYYY-MM-DD HH:MM:SS` 字串，方便直接接軌各類 DB Viewers、Pandas 連線與 Dashboard 圖表直觀呈現。
5.  **🖥️ 現代化 Web Dashboard**
    *   採用 **磨砂玻璃化 (Glassmorphic)** 暗黑系設計語彙，並帶有霓虹光影點綴。
    *   自動刷新各錶存量最後日期，提供一鍵 **Sync Now** 手動異步派送同步。

---

## ⚙️ 安裝環境與配額

### 1. 安裝 Dependencies
```bash
pip install -r requirements.txt
```

### 2. 環境變數設定 (`.env`)
於專案根目錄下建立 `.env` 檔案，並配置您的 Shioaji 憑證與想要追蹤的合約：

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

*   **服務位址**: 啟動後默認監聽 `http://127.0.0.1:8000`。
*   **管理面盤**: 打開瀏覽器訪問 `http://127.0.0.1:8000/` 即可。

---

## 📁 專案架構概覽

*   `app/main.py` - FastAPI 服務入口，設定靜態檔案伺服、API`/api/status` 以及手動同步`/api/sync`。
*   `download_futures_data.py` - 核心邏輯層：SQL 表格初始建立、續傳檢查、API 斷點下補、Pandas Resample 聚合對齊及寫入儲存。
*   `scheduler_manager.py` - 背景調度中心，包裹 `APScheduler` 初始化例行 cron 計算等。
*   `frontend/` - 放置 `dashboard.html` 網頁靜態。
*   `Shioaji.db` - SQLite 存儲檔案（自初始化運行後自動連線生成）。

---

## 🔗 API 端點參考

| 方法 | 端點 | 說明 |
| :--- | :--- | :--- |
| `GET` | `/` | 渲染 Web Dashboard UI |
| `GET` | `/api/status` | 讀取 SQLite 表格最後一筆 timestamp 整合狀況與 Shioaji 通訊狀況 |
| `POST` | `/api/sync` | 背景手動異步觸發 Full-Sync 下載同步 (同排程補齊模式) |

---
