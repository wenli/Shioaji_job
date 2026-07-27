# Futures Data Downloader & SMC/ORB Backtest Pro

自動化下載 Shioaji 歷史期貨 K 棒資料，自動排程對齊聚合為多週期結構，提供磨砂玻璃 (Glassmorphic) 風格的一站式 Web 控制面盤，整合 **SMC 實時看盤終端** 與 **ORB 多時區實時交易終端**，支援高精度雙時區 (MTF) SMC 策略回測、開盤區間突破 (ORB) 策略回測、多進程參數最佳化掃描、**ORB 突破過濾管道 (Filter Pipeline)** 與零風險模擬交易 (Paper Trading)。

> [!IMPORTANT]
> 📖 **[最新！系統使用與操作手冊 (user_guide.md)](docs/user_guide.md)** 已正式發布！手冊內嵌了自動化瀏覽器擷取的系統實時運行畫面截圖，並詳細解說了策略配置與關鍵優化風控參數，建議優先點擊閱讀。

---

## 🌟 特色功能

### 1. 🗂️ 多週期支援與收盤時間戳對齊 (Multi-Timeframes & End-Time Stamping)
* 原生下載 Shioaji 最細顆粒度 **1K** 資料。
* **統一收盤時間標記**：**5K, 15K, 30K, 60K** 各週期聚合後，其時間戳均平移至**該區間的收盤時間（右端點）**儲存，保持資料庫時間標記邏輯一致，與主流金融分析終端對齊。
* 其中 **60K (台指期 60 分K)** 採用符合標準開盤分箱的 `closed='left', label='left'` 並平移 `+60分鐘`，日盤與夜盤獨立 Resample，徹底防止開盤首分鐘（08:45）資訊遺漏。

### 2. 📊 雙時區 SMC 策略量化回測 (防止未來偏差)
* **天然無未來偏差對齊**：由於大週期（5K）在資料庫中已使用收盤時間作為時間戳標記，回測時大週期指標的發布時間天然代表「該 K 線收盤時刻」，配合 `pd.merge_asof(..., direction='backward')` 時即能在無需額外執行時平移下自然對齊，徹底消除「偷看大週期未來價格」的 Look-Ahead Bias。
* **實盤手續費與稅金摩擦**：小台指 (MTX) 每筆雙邊收取 **NT$ 40 固定手續費**，大台指 (TX) 每筆雙邊收取 **NT$ 100 固定手續費**，並依進出場價格嚴格課徵 **0.002% 期交稅**。
* **主力交易策略支援**：
  * 🦄 **Unicorn Model (台指獨角獸)**：5K 流動性獵取後，1K 發生結構轉變 (CHoCH)，回測 1K 破壞塊與 FVG 共振帶進場。
  * ⚡ **Silver Bullet (台指銀色子彈)**：在日/夜盤黃金交易時段捕捉高流動性下的高勝率交易機會。

### 3. 🛡️ ORB 突破過濾管道 (ORB Filter Pipeline) [NEW]
針對台指期假突破（False Breakout）頻繁之市場特性，打造 4 大多維度過濾管道，大幅提升開盤突破勝率與 Profit Factor：
1. **成交量突波過濾 (Volume Spike Filter)**：突破 K 棒之成交量必須達到前 N 根（預設 20）均量的 `vol_spike_ratio` 倍數（預設 1.5x），避免無量無力之假突破。
2. **動能門檻過濾 (Momentum Threshold Filter)**：針對日盤與夜盤波動特徵提供雙閥值控制（日盤 `mom_day_pct` 預設 0.05%、夜盤 `mom_night_pct` 預設 0.03%），要求突破爆發力達到門檻。
3. **VWAP 均價線過濾 (VWAP Align Filter)**：多頭突破時要求價格高於當日 VWAP 均價線，空頭突破時要求價格低於 VWAP，確保順應市場當日法人籌碼大方向。
4. **過度延伸保護 (Over-Extension Filter)**：檢查當前突破點距離開盤 ORB 區間與 ATR 的過度延伸距離（預設 `2.0x ATR` 及 `1.5x ORB`），預防進場在行情末端之追高殺低危險區。

### 4. 🎯 ORB 策略回測與多進程並行優化器
* **開盤區間突破 (ORB) 回測核心**：
  * 在 `tx_backtest.py` 中實現了獨立的 `ORBBacktestSimulator` 與 `app/strategy/orb_filters.py`。
  * 精確計算日盤 (08:45) 與夜盤 (15:00) 的開盤收集區間高低點與動態過濾檢驗。
  * **ATR 動態實體強度過濾器 (ATR Body Filter)**：要求突破 K 棒實體大於 ATR 指定倍數。
  * **保底 1 口交易機制 (Min 1 Lot Fallback)**：確保小資金帳戶在嚴格風控下不漏接訊號。
  * **多重停損計算模式 (SL Mode)**：
    * `bar_extreme`：突破當下之 K 棒極值停損。
    * `range_edge`：**開盤區間軌道停損（同側假突破即停損）**。實測使勝率大幅提升 6.83%，淨損益爆發性成長 4.3 倍！
    * `atr_dynamic`：動態 ATR 倍數停損。
    * `fixed_points`：固定點數防禦性停損。
* **多進程並行網格優化**：使用 `concurrent.futures.ProcessPoolExecutor` 多進程並行搜尋最優 ORB 參數組合，輸出 JSON 與 Markdown 優化報告。

### 5. 🦄 SMC MTF 實時交易看盤終端 (SMC MTF Live Trading Terminal)
* **2x2 四宮格 TradingView 互動畫布**：Web 前端採用輕量圖表 `lightweight-charts` 同步呈現 1K, 5K, 15K, 60K 實時 K棒生長。
* **Shioaji 實時串流行情 (Real-time Live Stream)**：秒級廣播 2x2 圖表與動態 SMC 指標（OB 水平帶、Sweep 標記、CHoCH 虛線）。
* **全球台北時區 Asia/Taipei (UTC+8) 強強對齊**：徹底阻絕海外瀏覽器時區偏移。

### 6. ⚡ ORB 多時區實時交易終端與對帳儀表板 (ORB Terminal & Diagnostics) [NEW]
* **60/40 比例科技感佈局**：左側 1K 主策略監控圖表，右側 5K 與 15K 趨勢圖表十字游標同步。
* **過濾管道 Glassmorphism 彈窗 modal**：控制列提供 **「🛡️ 過濾管道」** 彈窗按鈕，即時調整 4 大過濾機制參數並同步至 WebSocket / 重播引擎。
* **TradingView 引擎雙軌增量繪製**：採用 `.update()` 與 `.setData()` 智選機制，解決毫秒級推播中的 `Cannot update oldest data` 控制台警告。
* **零風險前端模擬交易 (Paper Trading)**：突破發動時自動彈出警報，動態劃設 Entry, SL, TP 線與實時 PnL 計算。
* **獨立覆盤詳情頁 (`orb_trade_detail.html`)**：提供「🛡️ ORB 突破過濾器驗證」專屬卡片，清晰還原每筆交易進場時的過濾器過關狀態與指標數據。

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

* **資料庫自動初始化與路徑統一**：系統啟動時會自動檢查並在您於 `.env` 中設定的 `DB_NAME` 資料庫路徑上完成 K 線表格初始化。
* **強健的行情模擬與時區安全**：前後端全面採用安全台北時區（Asia/Taipei）以防 Session 錯亂。

確保 `.env` 配置妥善後，直接執行主入口腳本：

```bash
python app/main.py
```

* **量化回測與控制面盤**: `http://127.0.0.1:8000/`
* **SMC 實時看盤交易終端**: `http://127.0.0.1:8000/live`
* **ORB 多時區實時交易終端**: `http://127.0.0.1:8000/orb_terminal` [NEW]

---

## 📖 使用與操作手冊

我們為您準備了圖文並茂的詳細系統說明，其中包含實際運行畫面截圖與關鍵風控優化機制說明：
👉 **[系統使用與操作手冊 (user_guide.md)](docs/user_guide.md)**

---

## 📁 專案架構概覽

* `docs/user_guide.md` - **系統使用與操作手冊**：內嵌主控制面板、SMC 與 ORB 實時終端運行截圖之詳細圖文操作教學。
* `app/strategy/orb_filters.py` - **ORB 突破過濾管道核心 [NEW]**：包含 `FilterConfig`, `FilterResult`, `VolumeSpikeFilter`, `MomentumFilter`, `VWAPFilter`, `OverExtensionFilter`, `ORBFilterPipeline`。
* `app/main.py` - FastAPI 服務入口，設定靜態檔案伺服、API 路由，以及**實時/重播 WebSocket 雙向推送與參數同步引擎**。
* `tx_backtest.py` - **回測與計算核心**：包含 MTF SMC 訊號引擎 (`TaiwanFuturesSMCEngine`)、`SMCBacktestSimulator`、`ORBBacktestSimulator`、多進程網格優化器 (`run_orb_parameter_optimization`) 與實時 ORB 計算模組。
* `download_futures_data.py` - 數據下載層：資料庫表格初始化、自動斷點續傳補齊、K 線 Resample 聚合對齊。
* `scheduler_manager.py` - 背景調度排程，管理 `APScheduler` 定時同步任務。
* `frontend/dashboard.html` - 回測一體化控制面盤，包含 ORB 過濾管道參數面板與損益點數/過濾標籤對帳表。
* `frontend/orb_trade_detail.html` - **ORB 獨立交易覆盤明細頁面 [NEW]**：內建「🛡️ ORB 突破過濾器驗證」診斷儀表板與 null-safe DOM 護甲。
* `frontend/live_terminal.html` - **SMC 實時看盤終端**：2x2 輕量圖表及訊號警報牆。
* `frontend/orb_terminal.html` - **ORB 實時交易終端 [NEW]**：60/40 游標同步圖表、開盤區間畫線、Filter Pipeline 彈窗 Modal 與前端模擬交易中心。
* `scripts/backtest/test_orb_filters.py` - **ORB 過濾管道單元測試腳本 [NEW]**：全方位驗證 4 大過濾器之多邏輯極限情境。
* `scripts/backtest/test_orb_calculator.py` - **ORB 實時計算單元測試腳本**：模擬 tick 行情並驗證核心計算邏輯。

---

## 🔗 API 與 WebSocket 端點參考

| 方法 | 端點 | 說明 |
| :--- | :--- | :--- |
| `GET` | `/` | 渲染 Web 一體化回測 Dashboard UI |
| `GET` | `/live` | 渲染 SMC 實時看盤交易終端網頁 UI |
| `GET` | `/orb_terminal` | 渲染 ORB 多時區實時交易終端網頁 UI [NEW] |
| `GET` | `/orb_trade_detail.html` | 渲染 ORB 獨立交易覆盤診斷對帳單頁面 [NEW] |
| `GET` | `/api/status` | 讀取 SQLite 整合狀況與 Shioaji 通訊狀況 |
| `POST` | `/api/sync` | 背景手動異步觸發 Full-Sync 下載同步 |
| `POST` | `/api/backtest` | 執行台指期雙時區 SMC 策略量化回測 |
| `POST` | `/api/backtest/optimize` | 執行獨角獸策略的二維參數優化 |
| `POST` | `/api/backtest/orb` | 執行台指期開盤區間突破 (ORB) 策略回測 [NEW] |
| `POST` | `/api/backtest/orb/optimize` | 執行並行多進程 ORB 網格最佳化優化 [NEW] |
| `WS` | `/api/live/ws` | **實時看盤 WebSocket 管道**，支援行情重播、模擬實時、Filter Pipeline 參數動態套用與 Shioaji 實時行情廣播 |
| `GET` | `/api/logs` | 讀取日誌端點 |
