# -*- coding: utf-8 -*-
"""
SMC 機構訂單塊 (Order Block) 專屬可視化回測 Web UI 獨立啟動器
功能：
1. 檢查資料庫與服務環境
2. 啟動 FastAPI / Uvicorn 服務
3. 自動於預設瀏覽器開啟 http://localhost:<port>/order_block
"""

import os
import sys
import time
import socket
import webbrowser
import threading
import uvicorn
from dotenv import load_dotenv

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))


def is_port_in_use(port: int) -> bool:
    """檢查指定連接埠是否已被佔用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def find_available_port(start_port: int = 8000, max_attempts: int = 20) -> int:
    """尋找可用連接埠"""
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(port):
            return port
    return start_port


def open_browser(url: str, delay: float = 1.5):
    """延遲在瀏覽器中開啟 Web UI"""
    time.sleep(delay)
    print(f"\n[🚀 正在開啟瀏覽器]: {url}")
    webbrowser.open(url)


def main():
    print("=" * 70)
    print("🏛️ SMC 機構訂單塊 (Order Block) 專屬回測 Web UI 啟動中...")
    print("=" * 70)

    db_name = os.getenv("DB_NAME", r"C:\Intel\Database\Shioaji-future.db")
    if os.path.exists(db_name):
        size_mb = os.path.getsize(db_name) / (1024 * 1024)
        print(f"✅ 成功辨識歷史期貨資料庫: {db_name} ({size_mb:.1f} MB)")
    else:
        print(f"⚠️ 警告: 未找到資料庫 {db_name}，請確認 .env 設定。")

    port = find_available_port(8000)
    url = f"http://localhost:{port}/order_block"

    print(f"🌐 服務網址: {url}")
    print("💡 提示: 按 Ctrl+C 可隨時終止服務")
    print("-" * 70)

    threading.Thread(target=open_browser, args=(url, 1.2), daemon=True).start()

    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
