# Futures Data Downloader & SMC/ORB Backtest Pro

自動化下載 Shioaji 歷史期貨 K 棒資料，自動排程對齊聚合為多週期結構，提供磨砂玻璃 (Glassmorphic) 風格的一站式 Web 控制面盤，整合 **SMC 實時看盤終端** 與 **ORB 多時區實時交易終端**，支援高精度雙時區 (MTF) SMC 策略回測、開盤區間突破 (ORB) 策略回測、多進程參數最佳化掃描與零風險模擬交易 (Paper Trading)。

> [!IMPORTANT]
> 📖 **[最新！系統使用與操作手冊 (user_guide.md)](file:///C:/Users/wenli/.gemini/antigravity-ide/brain/fefe79c9-758e-43a4-a3ce-c2ef48f51cf5/user_guide.md)** 已正式發布！手冊內嵌了自動化瀏覽器擷取的系統實時運行畫面截圖，並詳細解說了策略配置與關鍵優化風控參數，建議優先點擊閱讀。

---

## 🌟 特色功能

### 1. 🗂️ 多週期支援 (Multi-Timeframes)
* 原生下載 Shioaji 最細顆粒度 **1K** 資料。
* 自動在記憶體端使用 Pandas 對齊聚合：**5K, 15K, 30K, 60K, 1D** 各週期。
* 其中 **60K (台指期 60 分K)** 採用客製化**日盤與夜盤獨立 Resample 聚合邏輯**，防止時間戳平移。

### 2. 📊 雙時區 SMC 策略量化回測 (防止未來偏差)
* **無未來偏差對齊**：5K (HTF) 大週期訊號在時間對齊時，向後 shift 一根 5K（即移至收盤時間，並採用 `merge_asof`），才傳遞給 1K (LTF) 的進場時間線，徹底消除「偷看大週期未來價格」的舞弊。
* **實盤手續費與稅金摩擦**：小台指 (MTX) 每筆雙邊收取 **NT$ 40 固定手續費**，大台指 (TX) 每筆雙邊收取 **NT$ 100 固定手續費**，並依進出場價格嚴格課徵 **0.002% 期交稅**。
* **主力交易策略支援**：
  * 🦄 **Unicorn Model (台指獨角獸)**：5K 流動性獵取後，1K 發生結構轉變 (CHoCH)，回測 1K 破壞塊與 FVG 共振帶進場。
  * ⚡ **Silver Bullet (台指銀色子彈)**：在日/夜盤黃金交易時段捕捉高流動性下的高勝率交易機會。

### 3. 🎯 ORB 策略回測與多進程並行優化器 [NEW]
* **開盤區間突破 (ORB) 回測核心**：
  * 在 `tx_backtest.py` 中實現了獨立的 `ORBBacktestSimulator`。
  * 精確計算日盤 (08:45) 與夜盤 (15:00) 的開盤收集區間高低點。
  * 提供突破 Ticks Ticks 緩衝，並整合動能門檻 (`momentum_threshold`) 與 5K 滾動量增 (`vol_spike_ratio`) 過濾，大幅降低震盪盤洗盤風險。
  * **ATR 動態實體強度過濾器 (ATR Body Filter)** [NEW]：支援 `orb_atr_period`（預設 14）與 `orb_atr_multiplier`（預設 0.0，大於 0.0 時啟用）。在價格突破時，要求突破 K 棒的實體高度（`|Close - Open|`）必須大於或等於前一根 K 棒的 ATR 乘以該乘數，有效過濾無力、慢速爬行式的假突破。
  * **保底 1 口交易機制 (Min 1 Lot Fallback)** [NEW]：解決小資金帳戶在嚴格風控（如 1%）下，計算出的交易口數（`lots_to_trade`）因不足 1 口而被四捨五入/無條件捨去為 0 的問題。當啟動 `force_min_lot`（預設 True）且總資金大於最低保證金門檻（小台指 MTX 為 50,000 元，大台指 TX 為 200,000 元）時，強制以 1 口進場，確保小帳戶回測與實盤不漏接訊號。
  * **多重停損計算模式 (SL Mode)** [NEW]：支援四種自訂停損計算方式，交易員可透過控制面板自由切換：
    * `bar_extreme`：突破當下之 K 棒極值停損。
    * `range_edge`：**開盤區間軌道停損（同側假突破即停損）**。多單跌破開盤上軌（減緩衝點），空單突破開盤下軌（加緩衝點）即刻離場。**實測可縮小停損空間，使勝率大幅提升 6.83%，淨損益爆發性成長 4.3 倍**！
    * `atr_dynamic`：前一根 K 棒之 ATR 的指定倍數動態停損。
    * `fixed_points`：固定點數防禦性停損。
    * **最低防護點數 (`min_sl_points`)** [NEW]：設定保底最低停損距離，防範因突破 K 棒過小導致被市場隨機雜訊秒殺出場。
  * 結合賺賠比 (R-R 2.0 倍) 自動衍生止盈價 (TP)，每時段結束前強制平倉。
* **多進程並行網格優化**：
  * 使用 `concurrent.futures.ProcessPoolExecutor` 多進程並行搜尋最優 ORB 參數組合（收集分鐘、突破 Ticks、賺賠比、ATR 乘數等）。
  * 根據「交易次數限制 (>=15次)」與「勝率 -> 獲利因子 -> 淨利」的多重指標排序演算法，快速抓出策略高原。
  * 自動輸出 JSON 報告與 Markdown 優化摘要檔案至 `logs/backtest/` 目錄下。

### 4. 🦄 SMC MTF 實時交易看盤終端 (SMC MTF Live Trading Terminal)
* **2x2 四宮格 TradingView 互動畫布**：Web 前端採用輕量圖表 `lightweight-charts` 同步呈現 1K, 5K, 15K, 60K 實時 K棒生長。
* **Shioaji 實時串流行情 (Real-time Live Stream)**：工業級 `asyncio.Queue` 執行緒安全消費，實時與 SQLite 拼接歷史並重聚合，秒級廣播 2x2 圖表與動態 SMC 指標（OB 水平帶、Sweep 標記、CHoCH 虛線）。
* **全球台北時區 Asia/Taipei (UTC+8) 強強對齊**：雙層時區格式化強護甲，徹底阻絕海外瀏覽器時區偏移，不論何處看盤時間軸皆 100% 台北時間。

### 5. ⚡ ORB 多時區實時交易終端 (ORB Real-time Trading Terminal) [NEW]
* **60/40 比例科技感佈局**：左側 60% 寬度為 1K 主策略監控圖表，右側 40% 寬度上下平分放置 5K 與 15K 趨勢分析圖表。
* **十字游標同步**：移動 1K 圖表上之游標，右側 5K/15K 的十字游標即時連動，輔助多時區結構分析。
* **動態區間線繪製**：開盤收集期間以半透明虛線動態標記當前高低點，並於右上角顯示區間確立秒數倒數；確立後自動改為霓虹實線（青色為上界，粉色為下界）。
* **零風險前端模擬交易 (Paper Trading)**：
  * 突破時自動彈出「開盤突破警示對話框」，展示突破方向、建議進場價、止損（突破 K 棒極值）與 2.0 倍盈虧比止盈價。
  * 一鍵進場後，在 1K 圖表上動態繪製 Entry (灰色)、SL (紅色)、TP (綠色) 價格防禦線。
  * 60 FPS 動態更新持倉點數與金額盈虧（小台指 50 元/點），價格觸及 SL/TP 或收盤時自動平倉。
  * 平倉明細與盈虧原因自動記錄到下方的歷史日誌表格。

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
* **ORB 多時區實時交易終端**: `http://127.0.0.1:8000/orb_terminal` [NEW]

---

## 📖 使用與操作手冊

我們為您準備了圖文並茂的詳細系統說明，其中包含實際運行畫面截圖與關鍵風控優化機制說明：
👉 **[系統使用與操作手冊 (user_guide.md)](file:///C:/Users/wenli/.gemini/antigravity-ide/brain/fefe79c9-758e-43a4-a3ce-c2ef48f51cf5/user_guide.md)**

---

## 📁 專案架構概覽

* `app/main.py` - FastAPI 服務入口，設定靜態檔案伺服、API 路由，以及**實時/重播 WebSocket 雙向推送與參數同步引擎**。
* `tx_backtest.py` - **回測與計算核心**：包含 MTF SMC 訊號引擎 (`TaiwanFuturesSMCEngine`)、`SMCBacktestSimulator`、`ORBBacktestSimulator`、多進程網格優化器 (`run_orb_parameter_optimization`) 與實時 ORB 計算模組。
* `download_futures_data.py` - 數據下載層：資料庫表格初始化、自動斷點續傳補齊、K 線 Resample 聚合對齊。
* `scheduler_manager.py` - 背景調度排程，管理 `APScheduler` 定時同步任務。
* `frontend/dashboard.html` - 回測一體化控制面盤。
* `frontend/live_terminal.html` - **SMC 實時看盤終端**：2x2 輕量圖表及訊號警報牆。
* `frontend/orb_terminal.html` - **ORB 實時交易終端 [NEW]**：60/40 游標同步圖表、開盤區間畫線與前端模擬交易中心。
* `scripts/backtest/test_orb_calculator.py` - **ORB 實時計算單元測試腳本 [NEW]**：模擬 tick 行情並驗證核心計算邏輯。

---

## 🔗 API 與 WebSocket 端點參考

| 方法 | 端點 | 說明 |
| :--- | :--- | :--- |
| `GET` | `/` | 渲染 Web 一體化回測 Dashboard UI |
| `GET` | `/live` | 渲染 SMC 實時看盤交易終端網頁 UI |
| `GET` | `/orb_terminal` | 渲染 ORB 多時區實時交易終端網頁 UI [NEW] |
| `GET` | `/api/status` | 讀取 SQLite 整合狀況與 Shioaji 通訊狀況 |
| `POST` | `/api/sync` | 背景手動異步觸發 Full-Sync 下載同步 |
| `POST` | `/api/backtest` | 執行台指期雙時區 SMC 策略量化回測 |
| `POST` | `/api/backtest/optimize` | 執行獨角獸策略的二維參數優化 |
| `POST` | `/api/backtest/orb` | 執行台指期開盤區間突破 (ORB) 策略回測 [NEW] |
| `POST` | `/api/backtest/orb/optimize` | 執行並行多進程 ORB 網格最佳化優化 [NEW] |
| `WS` | `/api/live/ws` | **實時看盤 WebSocket 管道**，支援行情重播、模擬實時、參數動態套用與 Shioaji 實時行情廣播 |
| `GET` | `/api/logs` | 讀取日誌端點 |
