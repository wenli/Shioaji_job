# Design: 期貨歷史資料下載至 SQLite

## 📝 1. 背景與目標 (Background & Objective)
使用者希望開發一個功能：**下載 Shioaji Trading API 的期貨歷史 K 線 (KBars) 與 逐筆交易 (Ticks) 資料，並儲存至 SQLite 數據庫中。**
本文件提供數據表結構與下載流程的設計方案。

---

## 📊 2. 資料表結構設計 (Database Schema)

---

## 🔄 3. 數據下載流程與同步機制 (Data Flow & Sync)

### 📌 3.1 「一鍵同步」斷點續傳算法
* 針對不同時間週期的 K 線（如 1k, 5k, 15k, 30k, 60k, **1d**），**分拆為多張資料表**存儲（例如 `futures1k`、`futures15k`、`futures1d` 等），與系統既有架構保持一致。
1.  **查尾**：從目標資料表（如 `futures1k`）讀取該合約的 `MAX(ts)`（最後一筆時間戳）。
2.  **起點**：若無，則從預設起始日（如 `2024-01-01`）開始；若有，則以該時間戳作為 `start_date`。
3.  **終點**：`end_date` 設定為 **現在（Now）**。
4.  **補齊**：調用 `api.kbars` 抓取級距段落，排除 `INSERT OR IGNORE` 衝突、無縫堆疊。

---

## 🖥 4. Web UI 管理介面架構 (Web UI Architecture)

將由一個 Python 腳本（例如 `download_futures_data.py`）執行以下邏輯：

1. **Shioaji 登錄與合約確認**：
   * 登入後取得目標合約物件，推薦使用**連續月合約**（如 `api.Contracts.Futures.TXF.TXFR1`）獲取長週期資料。
2. **K 線下載 (`api.kbars`)**：
   * 透過調用 `api.kbars(contract=contract, start="YYYY-MM-DD", end="YYYY-MM-DD")` 傳入級距段落，獲取物件後批次匯入。
4. **批次插入與覆蓋控制**：
   * 搭配 Python `sqlite3` 的 `executemany` 與 `INSERT OR IGNORE` 迴避重複。
   * 會建立一個資料下載斷點機制（可查詢 SQLite 以續接），提升容錯。
5. **流量控管 (Rate Limits)**：
   * 下載間隔加入 `time.sleep`，配合 Shioaji `Quote Query` 50 requests/5 sec 頻率上限。

---

## ⏰ 5. 定時排程機制 (Automated Scheduler)

採用 **APScheduler** 架構，由後端 FastAPI 服務隨啟動運行：

*   **1k 任務**：Cron 設定為 `*/1 * * * *` （每分鐘）。為防止 Shioaji 伺服器 K 線未閉合，**加入 5 秒 Offset Delay**（於 05 秒觸發抓取）。
*   **5k / 60k 任務**：相應定時激活，或 1k 聚合而來。
*   **斷連異常處理**：排程觸發時若發現 `Shiioaji API` 斷線，自動重撥 `api.login()` 並接續。
*   **狀態廣播**：每次自動寫入成功，皆透過 WebSocket 通知前端刷新視圖或 Logs。

---

## 🖥 4. Web UI 管理介面架構 (Web UI Architecture)

系統將採取 **Backend-Frontend 獨立/輕合** 架構：

### 📌 4.1 後端 (FastAPI Backend)
*   **技術棧**：FastAPI + SQLModel (或 sqlite3 直接連線)
*   **主要 API 路由**：
    *   `GET /api/status`：查詢當前 SQLite 各 K線表格筆數、最近數據。
    *   `POST /api/download`：觸發背景任務 (BackgroundTasks) 下載特定級距。
    *   `WS /api/ws/logs`：使用 WebSocket 推播下載日誌。

### 📌 4.2 前端 (Glassmorphic Dashboard)
*   **風格預設**：**精緻深色玻璃擬態 (Premium Dark Glassmorphic)**，採用 Google Fonts (Outfit) 與微小動畫。
*   **介面佈局**：
    *   **頂部頂欄**：Shioaji 連線狀態 (🟢/🔴)、當前環境。
    *   **Stats 卡片列**：各表總筆數、數據佔用大小。
    *   **控制面板**：下拉選合約、選週期、時間段，以及「▶️ 開始下載」按鈕。
    *   **動態 Console**：即時 Log 滾動監視進度。

---

## ⚠️ 5. 注意事項 (Known Considerations)
*   **資料量大小**：雖然已排除 Tick，但 K 線累加後（若為多商品）仍有佔用空間，需定期檢查。
*   **SQLite 讀寫鎖**：確保 FastAPI 讀取與下載腳本寫入時，加上 Timeout 控制。
*   **異步處理**：背景任務下載，不卡死 Web UI 操作。
