# Tasks: 下載期貨歷史資料至 SQLite

- [ ] **1. 初始化與環境準備**
  - [ ] 1.1 確認 Shioaji API 連線與 `.env` 讀取
  - [x] 1.2 確認 `Shioaji.db` 數據庫連線（加入 Timeout 控制）

- [x] **2. 數據庫結構維護 (DDL)**
  - [x] 2.1 建立 `futures1k`, `futures5k`, `futures15k`, `futures30k`, `futures60k`, `futures1d` 表格
  - [x] 2.2 設定 `PRIMARY KEY (code, ts)` 避免數據重複

- [ ] **3. 下載核心邏輯開發 (Fetcher)**
  - [ ] 3.1 實作 `download_kbars(...)` API 全量接續邏輯
  - [ ] 3.2 實作「一鍵同步至最新」之斷點比對演算法 (`get_last_ts`)

- [x] **4. 排程管理 (Scheduler)**
  - [x] 4.1 整合 `APScheduler` 作為背景任務
  - [x] 4.2 建立每分鐘同步一次 1K, 並聚合多週期的排程 API
  - [ ] 4.3 加入 Rate Limit 控管 (`time.sleep`)

- [x] **5. 後端 API 開發 (FastAPI Backend)**
  - [x] 5.1 實作 `/api/status` 回傳各 Timeframe 最後更新時間
  - [x] 5.2 實作 `/api/sync` 觸發背景 Sync 任務
  - [x] 5.3 實作 Lifespan 託管 Shioaji 登入與 Scheduler 開始/停止

- [x] **6. 前端 UI 開發 (Frontend Dashboard)**
  - [x] 6.1 設計玻璃態 `dashboard.html` 視覺基礎與 API 串接
  - [x] 6.2 實作自動更新 Status 與 Sync 觸發按鈕按壓回饋控制面板控制下載、WebSocket 即時日誌同步

- [x] **7. 整合測試**
  - [x] 7.1 啟動雙端，視覺展示與 API 固定通訊
  - [x] 7.2 模擬連線斷開及錯誤狀態處理回饋

- [x] **8. 文檔維護**
  - [x] 8.1 填寫 `walkthrough.md` 與功能手冊
  - [x] 8.2 移除 Debug code, 標記 OpenSpec 功能驗證完畢 (Done)
