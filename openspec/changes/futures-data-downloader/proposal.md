# Proposal: 下載期貨歷史資料至 SQLite

## 📝 1. 背景與動機 (Background)
為了進行歷史回測與策略分析，需要將 Shioaji API 的期貨歷史 K 線（1k、5k、15k、30k、60k、**1d 日K**）資料下載並持久化儲存至本地 **SQLite (`Shioaji.db`)** 中，並提供一個 **Web UI 管理介面** 來監視狀態、手動**「一鍵同步至最新資料」**並驅動**「每分鐘定時自動更新」**功能。

## 🎯 2. 目標與範圍 (Scope)
*   **數據源**：Shioaji Trading API (使用連續月合約如 `TXFR1`)。
*   **儲存目標**：既有本地數據庫 `Shioaji.db`。
*   **儲存架構 (方案 B)**：
    *   **K 線**：依週期分拆表格，包含 `futures1k`、`futures5k`、`futures60k`，以及新增 **`futures1d`**。
    *   **管理面**：提供以 **FastAPI** 驅動、**Vanilla JS** 為底層的高級深色玻璃態 **Web 儀表板 Dashboard**。
*   **功能腳本**：建立一個獨立的 Python 腳本（例如 `download_futures_data.py`）執行下載與匯入。

## ✅ 3. 驗證計劃 (Verification)
1.  **運行下載**：執行腳本下載指定日期區間數據。
2.  **數據庫檢查**：使用 SQL 語法查詢 `futures1k`，確認數據筆數與欄位正確，且無重複項。
