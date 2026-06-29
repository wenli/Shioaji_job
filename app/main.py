import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import os
import pandas as pd
import logging
from pathlib import Path

# Custom Modules
import download_futures_data as dfd
import scheduler_manager as sm
import sqlite3

def get_db_connection() -> sqlite3.Connection:
    """Helper to connect to the database (configured via DB_NAME in .env or fallback)."""
    db_path = dfd.DB_NAME
    if not os.path.exists(db_path):
        fallback_path = r"C:\Intel\TW_Stock_K-Line_Chart\SK.db"
        if os.path.exists(fallback_path):
            db_path = fallback_path
    
    # Ensure directory exists if needed
    path_obj = Path(db_path)
    if path_obj.parent != Path("."):
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        
    return sqlite3.connect(db_path, timeout=30.0)

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Lifespan for Shioaji Init & Scheduler Startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Initialize SQLite database tables
    try:
        logger.info("Auto-initializing SQLite database tables...")
        dfd.init_db()
        logger.info("SQLite database tables initialized successfully.")
    except Exception as e:
        logger.exception(f"Failed to auto-initialize SQLite database: {e}")

    # 1. Initialize Shioaji Client
    api = dfd.get_shioaji_client()
    if api:
        app.state.api = api
        # 2. Start Background Scheduler
        sm.start_scheduler(api)
    else:
        logger.error("Lifespan could not login to Shioaji. Scheduler did not start.")
        
    yield
    # 3. Shutdown Scheduler
    sm.stop_scheduler()

app = FastAPI(title="Futures Data Downloader Dashboard", lifespan=lifespan)

# Setup Static Files for Frontend
frontend_path = Path("frontend")
frontend_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Serves the main Dashboard HTML."""
    html_file = frontend_path / "dashboard.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Frontend Dashboard (dashboard.html) not found.</h1>"

@app.get("/api/status")
def get_status():
    """Returns Database Connection & Latest Timestamps per timeframe."""
    code = os.getenv("TARGET_CODE", "TXFR1")
    intervals = ["1k", "5k", "15k", "30k", "60k", "1d"]
    stats = {}
    
    for iv in intervals:
        table = f"futures{iv}"
        ts = dfd.get_last_ts(table, code)
        if ts:
             formatted_ts = ts
        else:
             formatted_ts = "No Data"
        stats[iv] = formatted_ts
        
    return {
        "status": "Online" if hasattr(app.state, 'api') else "Offline/Unlogged",
        "code": code,
        "sync_stats": stats
    }

@app.post("/api/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    """Triggers a One-Key Sync manual backup in the background."""
    if not hasattr(app.state, 'api'):
         raise HTTPException(status_code=503, detail="Shioaji API is not logged in.")
         
    api = app.state.api
    code = os.getenv("TARGET_CODE", "TXFR1")
    
    try:
         # Resolving Contract
         if hasattr(api.Contracts.Futures, 'TXF') and hasattr(api.Contracts.Futures.TXF, code):
              contract = getattr(api.Contracts.Futures.TXF, code)
         else:
              contract = api.Contracts.Futures.TXF.TXFR1
    except AttributeError:
         raise HTTPException(status_code=500, detail="Contract TXF lookup failed.")

    # Add task to background tasks to return 200 immediately
    background_tasks.add_task(dfd.sync_to_latest, api, contract)
    
    return {"message": f"Manual sync triggered in background for {code}."}

@app.get("/api/logs")
def get_logs():
    """Stub endpoint for logs readout. In production, hook into log file tail or pipe."""
    # In a fully-blown setting, tail logs.txt
    return {"logs": "Logging streams will output to console/FastAPI console stream."}

# ==============================================================================
# SMC Backtest & Optimization API
# ==============================================================================
from pydantic import BaseModel
from typing import List, Optional
import tx_backtest as tb

class BacktestRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_capital: float = 1000000.0
    risk_pct: float = 0.01
    rr_ratio: float = 3.6
    min_sl: float = 20.0
    contract_type: str = "MTX"
    strategies: List[str] = ["unicorn_model", "silver_bullet"]
    session_filter: str = "both"
    tp_mode: str = "rr"

class OptimizeRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_capital: float = 1000000.0
    risk_pct: float = 0.01
    contract_type: str = "MTX"
    session_filter: str = "both"

class ORBOptimizeRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_capital: float = 1000000.0
    risk_pct: float = 0.01
    contract_type: str = "MTX"
    session_filter: str = "both"
    force_min_lot: bool = True

class ORBBacktestRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_capital: float = 1000000.0
    risk_pct: float = 0.01
    contract_type: str = "MTX"
    session_filter: str = "both"
    orb_probe_minutes: int = 15
    orb_breakout_ticks: int = 5
    momentum_threshold: float = 0.0003
    vol_spike_ratio: float = 1.2
    rr_ratio: float = 2.0
    orb_atr_period: int = 14
    orb_atr_multiplier: float = 0.0
    force_min_lot: bool = True
    sl_mode: str = "bar_extreme"
    min_sl_points: float = 20.0
    fixed_sl_points: float = 30.0

@app.post("/api/backtest")
def run_backtest(req: BacktestRequest):
    try:
        # 1. 載入資料
        df_1k, df_5k = tb.load_real_data(
            code='TXFR1', 
            start_date=req.start_date, 
            end_date=req.end_date
        )
        if df_1k.empty or df_5k.empty:
            raise HTTPException(status_code=400, detail="所選日期區間無行情數據。")
            
        # 2. 計算 5K (HTF) SMC 訊號
        df_5k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_5k)
        
        # 3. 對齊合併
        df_merged = tb.TaiwanFuturesSMCEngine.align_and_merge_timeframes(df_1k, df_5k_analyzed)
        
        # 4. 初始化模擬器
        simulator = tb.SMCBacktestSimulator(
            df_merged, 
            start_capital=req.start_capital, 
            contract_type=req.contract_type
        )
        simulator.risk_pct = req.risk_pct
        
        results = {}
        curves = {}
        all_trades = []
        
        # 5. 執行策略
        for strat in req.strategies:
            if strat not in ['unicorn_model', 'silver_bullet', 'turtle_soup', 'rote']:
                continue
            metrics, trades, curve = simulator.run_strategy(
                strat, 
                rr_ratio=req.rr_ratio, 
                min_sl=req.min_sl,
                session_filter=req.session_filter,
                tp_mode=req.tp_mode
            )
            
            # 格式化權益曲線的 timestamp 為字串以便 JSON 序列化
            formatted_curve = [{"time": str(pt["time"]), "equity": pt["equity"]} for pt in curve]
            
            # 格式化交易流水中無法 JSON 序列化的 timestamp 為字串
            formatted_trades = []
            for t in trades:
                t_copy = t.copy()
                t_copy["entry_time"] = str(t_copy["entry_time"])
                t_copy["exit_time"] = str(t_copy["exit_time"])
                formatted_trades.append(t_copy)
                
            results[strat] = {
                "metrics": metrics,
                "trades_count": len(trades)
            }
            curves[strat] = formatted_curve
            all_trades.extend(formatted_trades)
            
        all_trades_sorted = sorted(all_trades, key=lambda x: x['entry_time'])
        
        return {
            "success": True,
            "results": results,
            "curves": curves,
            "trades": all_trades_sorted,
            "data_info": {
                "1k_rows": len(df_1k),
                "5k_rows": len(df_5k),
                "start_time": str(df_1k['datetime'].min()),
                "end_time": str(df_1k['datetime'].max())
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("回測執行失敗")
        raise HTTPException(status_code=500, detail=f"回測失敗: {str(e)}")

@app.post("/api/backtest/optimize")
def run_backtest_optimize(req: OptimizeRequest):
    try:
        # 1. 載入資料
        df_1k, df_5k = tb.load_real_data(
            code='TXFR1', 
            start_date=req.start_date, 
            end_date=req.end_date
        )
        if df_1k.empty or df_5k.empty:
            raise HTTPException(status_code=400, detail="所選日期區間無行情數據。")
            
        # 2. 計算 5K (HTF) SMC 訊號
        df_5k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_5k)
        
        # 3. 對齊合併
        df_merged = tb.TaiwanFuturesSMCEngine.align_and_merge_timeframes(df_1k, df_5k_analyzed)
        
        # 4. 執行優化
        best_rr, best_sl, best_metrics, opt_results = tb.run_parameter_optimization(
            df_merged, 
            contract_type=req.contract_type,
            start_capital=req.start_capital,
            risk_pct=req.risk_pct,
            session_filter=req.session_filter
        )
        
        return {
            "success": True,
            "best_rr": best_rr,
            "best_sl": best_sl,
            "best_metrics": best_metrics,
            "opt_results": opt_results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("參數優化執行失敗")
        raise HTTPException(status_code=500, detail=f"優化失敗: {str(e)}")

@app.post("/api/backtest/orb/optimize")
def run_orb_optimize(req: ORBOptimizeRequest):
    try:
        # 1. 載入資料 (只需要 1K 資料)
        df_1k, _ = tb.load_real_data(
            code='TXFR1', 
            start_date=req.start_date, 
            end_date=req.end_date
        )
        if df_1k.empty:
            raise HTTPException(status_code=400, detail="所選日期區間無行情數據。")
            
        # 2. 執行並行優化
        opt_results, json_path, md_path = tb.run_orb_parameter_optimization(
            df_1k,
            contract_type=req.contract_type,
            start_capital=req.start_capital,
            risk_pct=req.risk_pct,
            session_filter=req.session_filter,
            force_min_lot=req.force_min_lot
        )
        
        # 取出最佳組合
        best_combination = opt_results[0] if len(opt_results) > 0 else None
        
        return {
            "success": True,
            "best_combination": best_combination,
            "report_paths": {
                "json": json_path,
                "markdown": md_path
            },
            "opt_results": opt_results
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ORB 參數優化執行失敗")
        raise HTTPException(status_code=500, detail=f"優化失敗: {str(e)}")

@app.post("/api/backtest/orb")
def run_orb_backtest(req: ORBBacktestRequest):
    try:
        # 1. 載入資料
        df_1k, _ = tb.load_real_data(
            code='TXFR1', 
            start_date=req.start_date, 
            end_date=req.end_date
        )
        if df_1k.empty:
            raise HTTPException(status_code=400, detail="所選日期區間無行情數據。")
            
        # 2. 初始化模擬器
        simulator = tb.ORBBacktestSimulator(
            df_1k,
            start_capital=req.start_capital,
            contract_type=req.contract_type
        )
        simulator.risk_pct = req.risk_pct
        
        # 3. 執行策略
        metrics, trades, curve = simulator.run_strategy(
            orb_probe_minutes=req.orb_probe_minutes,
            orb_breakout_ticks=req.orb_breakout_ticks,
            momentum_threshold=req.momentum_threshold,
            vol_spike_ratio=req.vol_spike_ratio,
            rr_ratio=req.rr_ratio,
            session_filter=req.session_filter,
            orb_atr_period=req.orb_atr_period,
            orb_atr_multiplier=req.orb_atr_multiplier,
            force_min_lot=req.force_min_lot,
            sl_mode=req.sl_mode,
            min_sl_points=req.min_sl_points,
            fixed_sl_points=req.fixed_sl_points
        )
        
        # 格式化
        formatted_curve = [{"time": str(pt["time"]), "equity": pt["equity"]} for pt in curve]
        formatted_trades = []
        for t in trades:
            t_copy = t.copy()
            t_copy["entry_time"] = str(t_copy["entry_time"])
            t_copy["exit_time"] = str(t_copy["exit_time"])
            formatted_trades.append(t_copy)
            
        return {
            "success": True,
            "metrics": metrics,
            "trades": formatted_trades,
            "curve": formatted_curve,
            "data_info": {
                "1k_rows": len(df_1k),
                "start_time": str(df_1k['datetime'].min()),
                "end_time": str(df_1k['datetime'].max())
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ORB 回測執行失敗")
        raise HTTPException(status_code=500, detail=f"回測失敗: {str(e)}")

@app.get("/api/backtest/trade_chart")
def get_trade_chart(entry_time: str, exit_time: str, pre_bars: int = 120, post_bars: int = 60):
    import sqlite3
    try:
        # 強制轉為整數防 SQL 注入
        pre_bars = int(pre_bars)
        post_bars = int(post_bars)

        try:
            conn = get_db_connection()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"無法連接資料庫: {str(e)}")
        
        # 1. 取得 1K 範圍
        c = conn.cursor()
        c.execute(
            f"SELECT ts FROM futures1k WHERE code='TXFR1' AND ts < ? ORDER BY ts DESC LIMIT {pre_bars};",
            (entry_time,)
        )
        rows_pre = c.fetchall()
        start_ts_1k = rows_pre[-1][0] if rows_pre else entry_time
        
        c.execute(
            f"SELECT ts FROM futures1k WHERE code='TXFR1' AND ts > ? ORDER BY ts ASC LIMIT {post_bars};",
            (exit_time,)
        )
        rows_post = c.fetchall()
        end_ts_1k = rows_post[-1][0] if rows_post else exit_time
        
        df_1k = pd.read_sql_query(
            "SELECT ts, Open as open, High as high, Low as low, Close as close FROM futures1k WHERE code='TXFR1' AND ts >= ? AND ts <= ? ORDER BY ts ASC;",
            conn,
            params=(start_ts_1k, end_ts_1k)
        )
        
        # 2. 取得 5K 範圍
        c.execute(
            f"SELECT ts FROM futures5k WHERE code='TXFR1' AND ts < ? ORDER BY ts DESC LIMIT {pre_bars};",
            (entry_time,)
        )
        rows_pre_5k = c.fetchall()
        start_ts_5k = rows_pre_5k[-1][0] if rows_pre_5k else entry_time
        
        c.execute(
            f"SELECT ts FROM futures5k WHERE code='TXFR1' AND ts > ? ORDER BY ts ASC LIMIT {post_bars};",
            (exit_time,)
        )
        rows_post_5k = c.fetchall()
        end_ts_5k = rows_post_5k[-1][0] if rows_post_5k else exit_time
        
        df_5k = pd.read_sql_query(
            "SELECT ts, Open as open, High as high, Low as low, Close as close FROM futures5k WHERE code='TXFR1' AND ts >= ? AND ts <= ? ORDER BY ts ASC;",
            conn,
            params=(start_ts_5k, end_ts_5k)
        )
        
        conn.close()
        
        return {
            "success": True,
            "kbars_1k": df_1k.to_dict(orient="records"),
            "kbars_5k": df_5k.to_dict(orient="records")
        }
    except Exception as e:
        logger.exception("讀取交易K線圖數據失敗")
        raise HTTPException(status_code=500, detail=f"讀取圖表數據失敗: {str(e)}")

@app.get("/trade_detail", response_class=HTMLResponse)
async def get_trade_detail_page():
    """Serves the Trade Review Candlestick Dashboard HTML."""
    html_file = frontend_path / "trade_detail.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Trade Review Page (trade_detail.html) not found.</h1>"

@app.get("/orb_trade_detail", response_class=HTMLResponse)
async def get_orb_trade_detail_page():
    """Serves the ORB Trade Review Candlestick Dashboard HTML."""
    html_file = frontend_path / "orb_trade_detail.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>ORB Trade Review Page (orb_trade_detail.html) not found.</h1>"


@app.get("/live", response_class=HTMLResponse)
async def get_live_terminal():
    """Serves the SMC Real-time Trading Terminal HTML."""
    html_file = frontend_path / "live_terminal.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Live Terminal (live_terminal.html) not found.</h1>"

@app.get("/orb_terminal", response_class=HTMLResponse)
async def get_orb_terminal():
    """Serves the ORB Real-time Trading Terminal HTML."""
    html_file = frontend_path / "orb_terminal.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>ORB Terminal (orb_terminal.html) not found.</h1>"

# ==============================================================================
# WebSocket Real-Time Pushing & Replay Simulator Engine
# ==============================================================================
def get_session(dt):
    t = dt.time()
    if (t >= datetime.strptime("08:45", "%H:%M").time()) and (t <= datetime.strptime("13:45", "%H:%M").time()):
        return 'day'
    else:
        return 'night'

from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import json
from datetime import datetime, timedelta
import sqlite3

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()
active_replay_task = None

class RealTimeQuoteStreamer:
    """
    Shioaji 實盤行情串流管理器。
    採用多執行緒安全 asyncio.Queue，並與 SQLite 歷史 1K K線數據拼接，
    實時計算 1K/5K/15K/60K SMC 指標並向前端廣播。
    """
    def __init__(self):
        self.api = None
        self.queue = asyncio.Queue()
        self.worker_task = None
        self.df_1k_real = pd.DataFrame()
        self.subscribed_contract = None
        self.is_active = False
        self.loop = None
        self.orb_params = {
            "orb_probe_minutes": 15,
            "orb_breakout_ticks": 5,
            "momentum_threshold": 0.0003,
            "vol_spike_ratio": 1.2,
            "orb_atr_period": 14,
            "orb_atr_multiplier": 0.0
        }

    def start_stream(self, api) -> bool:
        if self.is_active:
            logger.info("RealTimeQuoteStreamer: Shioaji real-time stream already active.")
            return True
            
        self.api = api
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.get_event_loop()
        
        # 1. 預加載最近 1K 歷史數據以確保 SMC 指標可計算
        logger.info("RealTimeQuoteStreamer: Pre-loading 1K history data from DB...")
        try:
            conn = get_db_connection()
            # 載入最近 2500 筆 1K 數據 (約為 2 天完整交易日數據)
            query = "SELECT ts, Open as open, High as high, Low as low, Close as close, Volume as volume FROM futures1k WHERE code='TXFR1' ORDER BY ts DESC LIMIT 2500;"
            df_hist = pd.read_sql_query(query, conn)
            conn.close()
            
            if df_hist.empty:
                logger.error("RealTimeQuoteStreamer: No historical 1K data found in DB. Cannot init stream.")
                return False
                
            # 反轉使其按時間正序排列
            self.df_1k_real = df_hist.iloc[::-1].copy().reset_index(drop=True)
            self.df_1k_real['datetime'] = pd.to_datetime(self.df_1k_real['ts'])
            self.df_1k_real = self.df_1k_real.drop(columns=['ts'])
            self.df_1k_real['session'] = self.df_1k_real['datetime'].apply(get_session)
            logger.info(f"RealTimeQuoteStreamer: Preloaded {len(self.df_1k_real)} bars. Last timestamp: {self.df_1k_real['datetime'].iloc[-1]}")
        except Exception as e:
            logger.exception("RealTimeQuoteStreamer: Preload history failed")
            return False

        # 2. 啟動背景協程處理器
        self.queue = asyncio.Queue()
        self.worker_task = asyncio.create_task(self._process_ticks_worker())
        
        # 3. 訂閱 Shioaji 台指期連續近月合約
        try:
            import shioaji as sj
            # 優先透過 TXF.TXFR1 取得近月，其次從 Futures.TXFR1 尋找
            if hasattr(api.Contracts.Futures, 'TXF') and hasattr(api.Contracts.Futures.TXF, 'TXFR1'):
                 contract = api.Contracts.Futures.TXF.TXFR1
            elif hasattr(api.Contracts.Futures, 'TXFR1'):
                 contract = getattr(api.Contracts.Futures, 'TXFR1')
            else:
                 contract = api.Contracts.Futures.TXF.TXFR1
        except Exception as e:
            logger.error(f"RealTimeQuoteStreamer: Contract TXFR1 lookup failed: {e}")
            if self.worker_task:
                 self.worker_task.cancel()
            return False
                
        self.subscribed_contract = contract
        
        # 4. 註冊回調與啟動行情訂閱
        try:
            api.quote.set_on_tick_fop_v1_callback(self.on_tick)
            api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.Tick)
            self.is_active = True
            logger.info(f"RealTimeQuoteStreamer: Subscribed to {contract.code} tick stream successfully.")
            return True
        except Exception as e:
            logger.error(f"RealTimeQuoteStreamer: Shioaji subscribe failed: {e}")
            if self.worker_task:
                self.worker_task.cancel()
            return False

    def on_tick(self, exchange, tick):
        """
        Shioaji 背景執行緒觸發的 Callback。
        線程安全地將 Tick 數據投遞至協程 Queue。
        """
        if not self.is_active or not self.loop:
            return
            
        try:
            # 包裝 Tick 數據
            tick_data = {
                "code": tick.code,
                "datetime": tick.datetime,
                "close": float(tick.close),
                "volume": int(tick.volume),
            }
            # 線程安全遞送
            self.loop.call_soon_threadsafe(self.queue.put_nowait, tick_data)
        except Exception as e:
            logger.error(f"RealTimeQuoteStreamer Callback Error: {e}")

    async def _process_ticks_worker(self):
        """
        消費協程：處理 Tick、拼接歷史 1K K線、聚合多週期並計算 SMC，最後廣播。
        """
        logger.info("RealTimeQuoteStreamer: Queue worker active.")
        try:
            while True:
                tick = await self.queue.get()
                try:
                    tick_dt = pd.to_datetime(tick['datetime'])
                    tick_close = tick['close']
                    tick_vol = tick['volume']
                    
                    last_idx = len(self.df_1k_real) - 1
                    last_bar_dt = self.df_1k_real.loc[last_idx, 'datetime']
                    
                    # 跨分鐘換棒與同分鐘更新邏輯
                    tick_minute = tick_dt.replace(second=0, microsecond=0)
                    last_bar_minute = last_bar_dt.replace(second=0, microsecond=0)
                    
                    if tick_minute != last_bar_minute and tick_dt > last_bar_dt:
                        # 新建一根 1K 蠟燭棒
                        new_row = pd.DataFrame([{
                            'open': tick_close,
                            'high': tick_close,
                            'low': tick_close,
                            'close': tick_close,
                            'volume': tick_vol,
                            'datetime': tick_minute,
                            'session': get_session(tick_minute)
                        }])
                        self.df_1k_real = pd.concat([self.df_1k_real, new_row]).reset_index(drop=True)
                        logger.info(f"🟢 [Real Live Stream] 跨越至新分鐘 K線: {tick_minute} | 價格: {tick_close}")
                        
                        # 保持緩衝區大小 (維持約 2 天長度)
                        if len(self.df_1k_real) > 3000:
                            self.df_1k_real = self.df_1k_real.iloc[-2500:].reset_index(drop=True)
                    else:
                        # 更新當前 1K 蠟燭棒
                        self.df_1k_real.loc[last_idx, 'close'] = tick_close
                        self.df_1k_real.loc[last_idx, 'high'] = max(self.df_1k_real.loc[last_idx, 'high'], tick_close)
                        self.df_1k_real.loc[last_idx, 'low'] = min(self.df_1k_real.loc[last_idx, 'low'], tick_close)
                        self.df_1k_real.loc[last_idx, 'volume'] += tick_vol

                    # --- 實時聚合多時區 ---
                    df_1k_res = self.df_1k_real.copy()
                    df_1k_res.index = pd.to_datetime(df_1k_res['datetime'])
                    df_1k_res.index.name = 'ts_datetime'
                    df_1k_res['code'] = 'TXFR1'
                    df_1k_res['ts_datetime'] = df_1k_res.index
                    
                    df_5k_curr = dfd.aggregate_kbars(df_1k_res, "5min")
                    df_5k_curr['datetime'] = pd.to_datetime(df_5k_curr['ts'])
                    
                    df_15k_curr = dfd.aggregate_kbars(df_1k_res, "15min")
                    df_15k_curr['datetime'] = pd.to_datetime(df_15k_curr['ts'])
                    
                    df_60k_curr = dfd.resample_60k(df_1k_res)
                    df_60k_curr['datetime'] = pd.to_datetime(df_60k_curr['ts'])
                    
                    # --- 動態計算多時區 SMC 技術指標 ---
                    df_5k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_5k_curr)
                    df_15k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_15k_curr)
                    
                    df_1k_clean = self.df_1k_real.copy()
                    df_1k_clean['code'] = 'TXFR1'
                    df_merged_1k = tb.TaiwanFuturesSMCEngine.align_and_merge_timeframes(df_1k_clean, df_5k_analyzed)
                    
                    latest_1k_merged = df_merged_1k.iloc[-1]
                    latest_1k_bar = self.df_1k_real.iloc[-1].to_dict()
                    latest_1k_bar['datetime'] = str(latest_1k_bar['datetime'])
                    
                    # 1K Overlays
                    smc_1k = {
                        "choch_bullish": bool(latest_1k_merged.get('ltf_choch_bullish', False)),
                        "choch_bearish": bool(latest_1k_merged.get('ltf_choch_bearish', False)),
                        "choch_price": float(latest_1k_merged.get('last_pivot_h', latest_1k_merged['high'])) if latest_1k_merged.get('ltf_choch_bullish', False) else (float(latest_1k_merged.get('last_pivot_l', latest_1k_merged['low'])) if latest_1k_merged.get('ltf_choch_bearish', False) else None),
                        "fvg_bullish": bool(latest_1k_merged.get('ltf_fvg_bullish', False)),
                        "fvg_bearish": bool(latest_1k_merged.get('ltf_fvg_bearish', False)),
                        "fvg_top": float(latest_1k_merged['ltf_fvg_bull_top']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bull_top')) else (float(latest_1k_merged['ltf_fvg_bear_top']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bear_top')) else None),
                        "fvg_bottom": float(latest_1k_merged['ltf_fvg_bull_bottom']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bull_bottom')) else (float(latest_1k_merged['ltf_fvg_bear_bottom']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bear_bottom')) else None)
                    }
                    
                    # 5K Overlays
                    latest_5k = df_5k_analyzed.iloc[-1]
                    smc_5k = {
                        "sweep_low": bool(latest_5k.get('sweep_low', False)),
                        "sweep_high": bool(latest_5k.get('sweep_high', False)),
                        "ob_bullish_top": float(latest_5k['ob_bullish_top']) if not pd.isna(latest_5k.get('ob_bullish_top')) else None,
                        "ob_bullish_bottom": float(latest_5k['ob_bullish_bottom']) if not pd.isna(latest_5k.get('ob_bullish_bottom')) else None,
                        "ob_bearish_top": float(latest_5k['ob_bearish_top']) if not pd.isna(latest_5k.get('ob_bearish_top')) else None,
                        "ob_bearish_bottom": float(latest_5k['ob_bearish_bottom']) if not pd.isna(latest_5k.get('ob_bearish_bottom')) else None
                    }
                    
                    # 15K Overlays
                    latest_15k = df_15k_analyzed.iloc[-1]
                    smc_15k = {
                        "ob_bullish_top": float(latest_15k['ob_bullish_top']) if not pd.isna(latest_15k.get('ob_bullish_top')) else None,
                        "ob_bullish_bottom": float(latest_15k['ob_bullish_bottom']) if not pd.isna(latest_15k.get('ob_bullish_bottom')) else None,
                        "ob_bearish_top": float(latest_15k['ob_bearish_top']) if not pd.isna(latest_15k.get('ob_bearish_top')) else None,
                        "ob_bearish_bottom": float(latest_15k['ob_bearish_bottom']) if not pd.isna(latest_15k.get('ob_bearish_bottom')) else None
                    }
                    
                    # 60K Overlays
                    latest_60k = df_60k_curr.iloc[-1]
                    smc_60k = {
                        "pdh": float(latest_5k['pdh']) if not pd.isna(latest_5k.get('pdh')) else None,
                        "pdl": float(latest_5k['pdl']) if not pd.isna(latest_5k.get('pdl')) else None
                    }
                    
                    # 計算實時 ORB 狀態
                    orb_status = tb.calculate_realtime_orb_status(df_1k_clean, **self.orb_params)
                    
                    payload = {
                        "type": "live_tick",
                        "candles": {
                            "1k": {
                                "time": str(self.df_1k_real.loc[last_idx, 'datetime'].strftime('%Y-%m-%d %H:%M:%S')),
                                "open": float(latest_1k_bar['open']),
                                "high": float(latest_1k_bar['high']),
                                "low": float(latest_1k_bar['low']),
                                "close": float(latest_1k_bar['close']),
                                "volume": int(latest_1k_bar['volume']),
                                "smc": smc_1k
                            },
                            "5k": {
                                "time": str(latest_5k['ts']),
                                "open": float(latest_5k['open']),
                                "high": float(latest_5k['high']),
                                "low": float(latest_5k['low']),
                                "close": float(latest_5k['close']),
                                "volume": int(latest_5k['volume']),
                                "smc": smc_5k
                            },
                            "15k": {
                                "time": str(latest_15k['ts']),
                                "open": float(latest_15k['open']),
                                "high": float(latest_15k['high']),
                                "low": float(latest_15k['low']),
                                "close": float(latest_15k['close']),
                                "volume": int(latest_15k['volume']),
                                "smc": smc_15k
                            },
                            "60k": {
                                "time": str(latest_60k['ts']),
                                "open": float(latest_60k['open']),
                                "high": float(latest_60k['high']),
                                "low": float(latest_60k['low']),
                                "close": float(latest_60k['close']),
                                "volume": int(latest_60k['volume']),
                                "smc": smc_60k
                            }
                        },
                        "orb": orb_status
                    }
                    
                    # 廣播實時 Tick 與指標包給前端 TV 圖表
                    await manager.broadcast(payload)
                except Exception as e:
                    logger.error(f"RealTimeQuoteStreamer Error processing single tick: {e}")
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            logger.info("RealTimeQuoteStreamer: Consumer task was cancelled.")
        except Exception as e:
            logger.exception("RealTimeQuoteStreamer: Worker task crash")

    def stop_stream(self):
        if not self.is_active:
            return
            
        logger.info("RealTimeQuoteStreamer: Stopping and unsubscribing Shioaji stream...")
        try:
            import shioaji as sj
            if self.api and self.subscribed_contract:
                self.api.quote.unsubscribe(self.subscribed_contract, quote_type=sj.constant.QuoteType.Tick)
        except Exception as e:
            logger.error(f"RealTimeQuoteStreamer: Error unsubscribing: {e}")
            
        if self.worker_task:
            self.worker_task.cancel()
            self.worker_task = None
            
        self.is_active = False
        logger.info("RealTimeQuoteStreamer: Stream stopped successfully.")

real_time_streamer = RealTimeQuoteStreamer()

async def get_history_init_payload(df_1k_base: pd.DataFrame, orb_probe_minutes=15, orb_breakout_ticks=5, momentum_threshold=0.0003, vol_spike_ratio=1.2, orb_atr_period=14, orb_atr_multiplier=0.0) -> dict:
    """
    將預加載的最近 150 根 1K K線進行多時區聚合與全量 SMC 指標計算，
    清洗 NaN 數值為 None (null)，包裝成 history_init 封包返回給前端。
    """
    try:
        if df_1k_base.empty:
            return {}

        # 1. 準備聚合的 1K 數據
        df_1k_res = df_1k_base.copy()
        if 'session' not in df_1k_res.columns:
            df_1k_res['session'] = df_1k_res['datetime'].apply(get_session)
            
        df_1k_res.index = pd.to_datetime(df_1k_res['datetime'])
        df_1k_res.index.name = 'ts_datetime'
        df_1k_res['code'] = 'TXFR1'
        df_1k_res['ts_datetime'] = df_1k_res.index
        
        # 2. 多週期聚合
        df_5k = dfd.aggregate_kbars(df_1k_res, "5min")
        df_5k['datetime'] = pd.to_datetime(df_5k['ts'])
        
        df_15k = dfd.aggregate_kbars(df_1k_res, "15min")
        df_15k['datetime'] = pd.to_datetime(df_15k['ts'])
        
        df_60k = dfd.resample_60k(df_1k_res)
        df_60k['datetime'] = pd.to_datetime(df_60k['ts'])
        
        # 3. 全量計算各週期 SMC 指標
        df_5k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_5k)
        df_15k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_15k)
        
        df_1k_clean = df_1k_base.copy()
        df_1k_clean['code'] = 'TXFR1'
        df_merged_1k = tb.TaiwanFuturesSMCEngine.align_and_merge_timeframes(df_1k_clean, df_5k_analyzed)
        
        # 4. 包裝並清洗數據
        def clean_val(v):
            return None if pd.isna(v) else float(v)
            
        def clean_bool(v):
            return False if pd.isna(v) else bool(v)

        # 1K 歷史數據列表
        hist_1k = []
        for _, row in df_merged_1k.iterrows():
            choch_price = None
            if row.get('ltf_choch_bullish', False):
                choch_price = clean_val(row.get('last_pivot_h'))
            elif row.get('ltf_choch_bearish', False):
                choch_price = clean_val(row.get('last_pivot_l'))
                
            hist_1k.append({
                "time": str(row['datetime']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume']),
                "smc": {
                    "choch_bullish": clean_bool(row.get('ltf_choch_bullish')),
                    "choch_bearish": clean_bool(row.get('ltf_choch_bearish')),
                    "choch_price": choch_price,
                    "fvg_bullish": clean_bool(row.get('ltf_fvg_bullish')),
                    "fvg_bearish": clean_bool(row.get('ltf_fvg_bearish')),
                    "fvg_top": clean_val(row.get('ltf_fvg_bull_top')) if clean_bool(row.get('ltf_fvg_bullish')) else (clean_val(row.get('ltf_fvg_bear_top')) if clean_bool(row.get('ltf_fvg_bearish')) else None),
                    "fvg_bottom": clean_val(row.get('ltf_fvg_bull_bottom')) if clean_bool(row.get('ltf_fvg_bullish')) else (clean_val(row.get('ltf_fvg_bear_bottom')) if clean_bool(row.get('ltf_fvg_bearish')) else None),
                }
            })

        # 5K 歷史數據列表
        hist_5k = []
        for _, row in df_5k_analyzed.iterrows():
            hist_5k.append({
                "time": str(row['ts']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume']),
                "smc": {
                    "sweep_low": clean_bool(row.get('sweep_low')),
                    "sweep_high": clean_bool(row.get('sweep_high')),
                    "ob_bullish_top": clean_val(row.get('ob_bullish_top')),
                    "ob_bullish_bottom": clean_val(row.get('ob_bullish_bottom')),
                    "ob_bearish_top": clean_val(row.get('ob_bearish_top')),
                    "ob_bearish_bottom": clean_val(row.get('ob_bearish_bottom')),
                }
            })

        # 15K 歷史數據列表
        hist_15k = []
        for _, row in df_15k_analyzed.iterrows():
            hist_15k.append({
                "time": str(row['ts']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume']),
                "smc": {
                    "ob_bullish_top": clean_val(row.get('ob_bullish_top')),
                    "ob_bullish_bottom": clean_val(row.get('ob_bullish_bottom')),
                    "ob_bearish_top": clean_val(row.get('ob_bearish_top')),
                    "ob_bearish_bottom": clean_val(row.get('ob_bearish_bottom')),
                }
            })

        # 60K 歷史數據列表
        hist_60k = []
        pdh_val = None
        pdl_val = None
        if not df_5k_analyzed.empty:
            latest_5k = df_5k_analyzed.iloc[-1]
            pdh_val = clean_val(latest_5k.get('pdh'))
            pdl_val = clean_val(latest_5k.get('pdl'))
        
        for _, row in df_60k.iterrows():
            hist_60k.append({
                "time": str(row['ts']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": int(row['volume']),
                "smc": {
                    "pdh": pdh_val,
                    "pdl": pdl_val
                }
            })

        # 5. 計算歷史 ORB 區間線
        orb_history = tb.get_historical_orb_ranges(
            df_1k_res, 
            orb_probe_minutes=orb_probe_minutes, 
            orb_breakout_ticks=orb_breakout_ticks
        )

        return {
            "type": "history_init",
            "data": {
                "1k": hist_1k,
                "5k": hist_5k,
                "15k": hist_15k,
                "60k": hist_60k
            },
            "orb_history": orb_history
        }
    except Exception as e:
        logger.exception("Error generating history init payload")
        return {}

async def replay_simulator_worker(date_str: str, speed_seconds: float):
    """
    從資料庫讀取指定日期 (YYYY-MM-DD) 的 1K 數據，
    並以 speed_seconds (秒/K棒) 速度模擬重播生長，
    每步動態聚合 5K, 15K, 60K，重算 SMC 指標並廣播給前端。
    """
    try:
        logger.info(f"Replay worker started for {date_str} at speed {speed_seconds}s/bar")
        
        conn = get_db_connection()
        query = f"SELECT ts, Open as open, High as high, Low as low, Close as close, Volume as volume FROM futures1k WHERE code='TXFR1' AND ts >= '{date_str} 00:00:00' AND ts <= '{date_str} 23:59:59' ORDER BY ts;"
        df_1k_all = pd.read_sql_query(query, conn)
        conn.close()
        
        if df_1k_all.empty:
            logger.warning(f"No 1K data found in DB for date: {date_str}")
            await manager.broadcast({
                "type": "error",
                "message": f"所選日期 {date_str} 無行情數據，無法啟動重播。"
            })
            return
            
        logger.info(f"Loaded {len(df_1k_all)} 1K bars for replay on {date_str}")
        
        df_1k_all['datetime'] = pd.to_datetime(df_1k_all['ts'])
        df_1k_all['session'] = df_1k_all['datetime'].apply(get_session)
        start_idx = min(30, len(df_1k_all))
        
        for k in range(start_idx, len(df_1k_all) + 1):
            df_1k_curr = df_1k_all.iloc[:k].copy()
            latest_1k_bar = df_1k_curr.iloc[-1].to_dict()
            latest_1k_bar['datetime'] = str(latest_1k_bar['datetime'])
            
            # --- 實時聚合多週期 ---
            df_1k_res = df_1k_curr.copy()
            df_1k_res.index = pd.to_datetime(df_1k_res['datetime'])
            df_1k_res.index.name = 'ts_datetime'
            df_1k_res['code'] = 'TXFR1'
            df_1k_res['ts_datetime'] = df_1k_res.index
            
            df_5k_curr = dfd.aggregate_kbars(df_1k_res, "5min")
            df_5k_curr['datetime'] = pd.to_datetime(df_5k_curr['ts'])
            
            df_15k_curr = dfd.aggregate_kbars(df_1k_res, "15min")
            df_15k_curr['datetime'] = pd.to_datetime(df_15k_curr['ts'])
            
            df_60k_curr = dfd.resample_60k(df_1k_res)
            df_60k_curr['datetime'] = pd.to_datetime(df_60k_curr['ts'])
            
            # --- 動態計算各週期 SMC 指標 ---
            df_5k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_5k_curr)
            df_15k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_15k_curr)
            
            df_1k_clean = df_1k_curr.drop(columns=['ts']).rename(columns={'datetime': 'datetime'})
            df_1k_clean['code'] = 'TXFR1'
            df_merged_1k = tb.TaiwanFuturesSMCEngine.align_and_merge_timeframes(df_1k_clean, df_5k_analyzed)
            
            latest_1k_merged = df_merged_1k.iloc[-1]
            
            # 1K Overlays
            smc_1k = {
                "choch_bullish": bool(latest_1k_merged.get('ltf_choch_bullish', False)),
                "choch_bearish": bool(latest_1k_merged.get('ltf_choch_bearish', False)),
                "choch_price": float(latest_1k_merged.get('last_pivot_h', latest_1k_merged['high'])) if latest_1k_merged.get('ltf_choch_bullish', False) else (float(latest_1k_merged.get('last_pivot_l', latest_1k_merged['low'])) if latest_1k_merged.get('ltf_choch_bearish', False) else None),
                "fvg_bullish": bool(latest_1k_merged.get('ltf_fvg_bullish', False)),
                "fvg_bearish": bool(latest_1k_merged.get('ltf_fvg_bearish', False)),
                "fvg_top": float(latest_1k_merged['ltf_fvg_bull_top']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bull_top')) else (float(latest_1k_merged['ltf_fvg_bear_top']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bear_top')) else None),
                "fvg_bottom": float(latest_1k_merged['ltf_fvg_bull_bottom']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bull_bottom')) else (float(latest_1k_merged['ltf_fvg_bear_bottom']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bear_bottom')) else None)
            }
            
            # 2. 5K Overlays
            latest_5k = df_5k_analyzed.iloc[-1]
            smc_5k = {
                "sweep_low": bool(latest_5k.get('sweep_low', False)),
                "sweep_high": bool(latest_5k.get('sweep_high', False)),
                "ob_bullish_top": float(latest_5k['ob_bullish_top']) if not pd.isna(latest_5k.get('ob_bullish_top')) else None,
                "ob_bullish_bottom": float(latest_5k['ob_bullish_bottom']) if not pd.isna(latest_5k.get('ob_bullish_bottom')) else None,
                "ob_bearish_top": float(latest_5k['ob_bearish_top']) if not pd.isna(latest_5k.get('ob_bearish_top')) else None,
                "ob_bearish_bottom": float(latest_5k['ob_bearish_bottom']) if not pd.isna(latest_5k.get('ob_bearish_bottom')) else None
            }
            
            # 3. 15K Overlays
            latest_15k = df_15k_analyzed.iloc[-1]
            smc_15k = {
                "ob_bullish_top": float(latest_15k['ob_bullish_top']) if not pd.isna(latest_15k.get('ob_bullish_top')) else None,
                "ob_bullish_bottom": float(latest_15k['ob_bullish_bottom']) if not pd.isna(latest_15k.get('ob_bullish_bottom')) else None,
                "ob_bearish_top": float(latest_15k['ob_bearish_top']) if not pd.isna(latest_15k.get('ob_bearish_top')) else None,
                "ob_bearish_bottom": float(latest_15k['ob_bearish_bottom']) if not pd.isna(latest_15k.get('ob_bearish_bottom')) else None
            }
            
            # 4. 60K Overlays
            latest_60k = df_60k_curr.iloc[-1]
            smc_60k = {
                "pdh": float(latest_5k['pdh']) if not pd.isna(latest_5k.get('pdh')) else None,
                "pdl": float(latest_5k['pdl']) if not pd.isna(latest_5k.get('pdl')) else None
            }
            
            # 計算重播時的實時 ORB 狀態
            orb_status = tb.calculate_realtime_orb_status(df_1k_curr, **real_time_streamer.orb_params)
            
            payload = {
                "type": "replay_tick",
                "index": k,
                "total": len(df_1k_all),
                "candles": {
                    "1k": {
                        "time": str(latest_1k_bar['ts']),
                        "open": float(latest_1k_bar['open']),
                        "high": float(latest_1k_bar['high']),
                        "low": float(latest_1k_bar['low']),
                        "close": float(latest_1k_bar['close']),
                        "volume": int(latest_1k_bar['volume']),
                        "smc": smc_1k
                    },
                    "5k": {
                        "time": str(latest_5k['ts']),
                        "open": float(latest_5k['open']),
                        "high": float(latest_5k['high']),
                        "low": float(latest_5k['low']),
                        "close": float(latest_5k['close']),
                        "volume": int(latest_5k['volume']),
                        "smc": smc_5k
                    },
                    "15k": {
                        "time": str(latest_15k['ts']),
                        "open": float(latest_15k['open']),
                        "high": float(latest_15k['high']),
                        "low": float(latest_15k['low']),
                        "close": float(latest_15k['close']),
                        "volume": int(latest_15k['volume']),
                        "smc": smc_15k
                    },
                    "60k": {
                        "time": str(latest_60k['ts']),
                        "open": float(latest_60k['open']),
                        "high": float(latest_60k['high']),
                        "low": float(latest_60k['low']),
                        "close": float(latest_60k['close']),
                        "volume": int(latest_60k['volume']),
                        "smc": smc_60k
                    }
                },
                "orb": orb_status
            }
            
            await manager.broadcast(payload)
            await asyncio.sleep(speed_seconds)
            
        await manager.broadcast({
            "type": "status",
            "message": "重播播放完畢。"
        })
        logger.info("Replay worker finished successfully.")
    except asyncio.CancelledError:
        logger.info("Replay worker task was cancelled.")
    except Exception as e:
        logger.exception("Replay worker encountered an error")
        await manager.broadcast({
            "type": "error",
            "message": f"重播執行出錯: {str(e)}"
        })

async def simulated_live_ticks_worker():
    """
    載入最近的歷史行情，並以隨機 Tick 模式模擬 K棒實時上下跳動與收盤，
    每秒更新當前 1K 棒，動態聚合 5K, 15K, 60K 並進行即時 SMC 指標廣播。
    """
    try:
        logger.info("Simulated live ticks worker started.")
        conn = get_db_connection()
        # 載入最近 2500 筆 1K 數據 (約為 2 天完整交易日數據)
        query = "SELECT ts, Open as open, High as high, Low as low, Close as close, Volume as volume FROM futures1k WHERE code='TXFR1' ORDER BY ts DESC LIMIT 2500;"
        df_1k_recent = pd.read_sql_query(query, conn)
        conn.close()
        
        if df_1k_recent.empty:
            await manager.broadcast({"type": "error", "message": "無歷史基礎數據，無法啟動模擬實時。"})
            return
            
        df_1k_recent = df_1k_recent.iloc[::-1].copy()
        df_1k_recent['datetime'] = pd.to_datetime(df_1k_recent['ts'])
        df_1k_recent = df_1k_recent.drop(columns=['ts'])
        df_1k_recent['session'] = df_1k_recent['datetime'].apply(get_session)
        
        import random
        
        last_real_minute = datetime.now().minute
        
        while True:
            now_dt = datetime.now()
            last_bar_dt = df_1k_recent['datetime'].iloc[-1]
            
            if now_dt.minute != last_real_minute:
                last_real_minute = now_dt.minute
                new_dt = last_bar_dt + timedelta(minutes=1)
                last_close = df_1k_recent['close'].iloc[-1]
                new_row = pd.DataFrame([{
                    'open': last_close,
                    'high': last_close,
                    'low': last_close,
                    'close': last_close,
                    'volume': 0,
                    'datetime': new_dt,
                    'session': get_session(new_dt)
                }])
                df_1k_recent = pd.concat([df_1k_recent, new_row]).reset_index(drop=True)
                # 保持緩衝區大小 (維持約 2 天長度)
                if len(df_1k_recent) > 3000:
                    df_1k_recent = df_1k_recent.iloc[-2500:].reset_index(drop=True)
                
            last_idx = len(df_1k_recent) - 1
            curr_close = df_1k_recent.loc[last_idx, 'close']
            change = random.choice([-2, -1, 0, 1, 2, -3, 3])
            new_close = curr_close + change
            
            df_1k_recent.loc[last_idx, 'close'] = new_close
            df_1k_recent.loc[last_idx, 'high'] = max(df_1k_recent.loc[last_idx, 'high'], new_close)
            df_1k_recent.loc[last_idx, 'low'] = min(df_1k_recent.loc[last_idx, 'low'], new_close)
            df_1k_recent.loc[last_idx, 'volume'] += random.randint(5, 50)
            
            # --- 聚合計算 ---
            df_1k_res = df_1k_recent.copy()
            df_1k_res.index = pd.to_datetime(df_1k_res['datetime'])
            df_1k_res.index.name = 'ts_datetime'
            df_1k_res['code'] = 'TXFR1'
            df_1k_res['ts_datetime'] = df_1k_res.index
            
            df_5k_curr = dfd.aggregate_kbars(df_1k_res, "5min")
            df_5k_curr['datetime'] = pd.to_datetime(df_5k_curr['ts'])
            
            df_15k_curr = dfd.aggregate_kbars(df_1k_res, "15min")
            df_15k_curr['datetime'] = pd.to_datetime(df_15k_curr['ts'])
            
            df_60k_curr = dfd.resample_60k(df_1k_res)
            df_60k_curr['datetime'] = pd.to_datetime(df_60k_curr['ts'])
            
            df_5k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_5k_curr)
            df_15k_analyzed = tb.TaiwanFuturesSMCEngine.calculate_smc_htf_5k(df_15k_curr)
            
            df_1k_clean = df_1k_recent.copy()
            df_1k_clean['code'] = 'TXFR1'
            df_merged_1k = tb.TaiwanFuturesSMCEngine.align_and_merge_timeframes(df_1k_clean, df_5k_analyzed)
            
            latest_1k_merged = df_merged_1k.iloc[-1]
            latest_1k_bar = df_1k_recent.iloc[-1].to_dict()
            latest_1k_bar['datetime'] = str(latest_1k_bar['datetime'])
            
            smc_1k = {
                "choch_bullish": bool(latest_1k_merged.get('ltf_choch_bullish', False)),
                "choch_bearish": bool(latest_1k_merged.get('ltf_choch_bearish', False)),
                "choch_price": float(latest_1k_merged.get('last_pivot_h', latest_1k_merged['high'])) if latest_1k_merged.get('ltf_choch_bullish', False) else (float(latest_1k_merged.get('last_pivot_l', latest_1k_merged['low'])) if latest_1k_merged.get('ltf_choch_bearish', False) else None),
                "fvg_bullish": bool(latest_1k_merged.get('ltf_fvg_bullish', False)),
                "fvg_bearish": bool(latest_1k_merged.get('ltf_fvg_bearish', False)),
                "fvg_top": float(latest_1k_merged['ltf_fvg_bull_top']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bull_top')) else (float(latest_1k_merged['ltf_fvg_bear_top']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bear_top')) else None),
                "fvg_bottom": float(latest_1k_merged['ltf_fvg_bull_bottom']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bull_bottom')) else (float(latest_1k_merged['ltf_fvg_bear_bottom']) if not pd.isna(latest_1k_merged.get('ltf_fvg_bear_bottom')) else None)
            }
            
            latest_5k = df_5k_analyzed.iloc[-1]
            smc_5k = {
                "sweep_low": bool(latest_5k.get('sweep_low', False)),
                "sweep_high": bool(latest_5k.get('sweep_high', False)),
                "ob_bullish_top": float(latest_5k['ob_bullish_top']) if not pd.isna(latest_5k.get('ob_bullish_top')) else None,
                "ob_bullish_bottom": float(latest_5k['ob_bullish_bottom']) if not pd.isna(latest_5k.get('ob_bullish_bottom')) else None,
                "ob_bearish_top": float(latest_5k['ob_bearish_top']) if not pd.isna(latest_5k.get('ob_bearish_top')) else None,
                "ob_bearish_bottom": float(latest_5k['ob_bearish_bottom']) if not pd.isna(latest_5k.get('ob_bearish_bottom')) else None
            }
            
            latest_15k = df_15k_analyzed.iloc[-1]
            smc_15k = {
                "ob_bullish_top": float(latest_15k['ob_bullish_top']) if not pd.isna(latest_15k.get('ob_bullish_top')) else None,
                "ob_bullish_bottom": float(latest_15k['ob_bullish_bottom']) if not pd.isna(latest_15k.get('ob_bullish_bottom')) else None,
                "ob_bearish_top": float(latest_15k['ob_bearish_top']) if not pd.isna(latest_15k.get('ob_bearish_top')) else None,
                "ob_bearish_bottom": float(latest_15k['ob_bearish_bottom']) if not pd.isna(latest_15k.get('ob_bearish_bottom')) else None
            }
            
            latest_60k = df_60k_curr.iloc[-1]
            smc_60k = {
                "pdh": float(latest_5k['pdh']) if not pd.isna(latest_5k.get('pdh')) else None,
                "pdl": float(latest_5k['pdl']) if not pd.isna(latest_5k.get('pdl')) else None
            }
            
            # 計算模擬實時時的 ORB 狀態
            orb_status = tb.calculate_realtime_orb_status(df_1k_recent, **real_time_streamer.orb_params)
            
            payload = {
                "type": "live_tick",
                "candles": {
                    "1k": {
                        "time": str(df_1k_recent.loc[last_idx, 'datetime'].strftime('%Y-%m-%d %H:%M:%S')),
                        "open": float(latest_1k_bar['open']),
                        "high": float(latest_1k_bar['high']),
                        "low": float(latest_1k_bar['low']),
                        "close": float(latest_1k_bar['close']),
                        "volume": int(latest_1k_bar['volume']),
                        "smc": smc_1k
                    },
                    "5k": {
                        "time": str(latest_5k['ts']),
                        "open": float(latest_5k['open']),
                        "high": float(latest_5k['high']),
                        "low": float(latest_5k['low']),
                        "close": float(latest_5k['close']),
                        "volume": int(latest_5k['volume']),
                        "smc": smc_5k
                    },
                    "15k": {
                        "time": str(latest_15k['ts']),
                        "open": float(latest_15k['open']),
                        "high": float(latest_15k['high']),
                        "low": float(latest_15k['low']),
                        "close": float(latest_15k['close']),
                        "volume": int(latest_15k['volume']),
                        "smc": smc_15k
                    },
                    "60k": {
                        "time": str(latest_60k['ts']),
                        "open": float(latest_60k['open']),
                        "high": float(latest_60k['high']),
                        "low": float(latest_60k['low']),
                        "close": float(latest_60k['close']),
                        "volume": int(latest_60k['volume']),
                        "smc": smc_60k
                    }
                },
                "orb": orb_status
            }
            
            await manager.broadcast(payload)
            await asyncio.sleep(1.0)
            
    except asyncio.CancelledError:
        logger.info("Simulated live worker task was cancelled.")
    except Exception as e:
        logger.exception("Simulated live worker encountered an error")

@app.websocket("/api/live/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    global active_replay_task
    
    try:
        await websocket.send_json({
            "type": "status",
            "message": "實時看盤 WebSocket 連線成功。"
        })
        
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            action = data.get("action")
            
            if action == "start_replay":
                if active_replay_task and not active_replay_task.done():
                    active_replay_task.cancel()
                real_time_streamer.stop_stream()
                    
                date_val = data.get("date", "2026-05-20")
                speed_val = float(data.get("speed", 1.0))
                
                active_replay_task = asyncio.create_task(
                    replay_simulator_worker(date_val, speed_val)
                )
                await websocket.send_json({
                    "type": "status",
                    "message": f"已啟動 {date_val} 行情重播，速度 {speed_val}秒/根。"
                })
                
            elif action == "pause_replay" or action == "stop_replay":
                if active_replay_task and not active_replay_task.done():
                    active_replay_task.cancel()
                real_time_streamer.stop_stream()
                await websocket.send_json({
                    "type": "status",
                    "message": "行情播放/實時模式已停止。"
                })
                    
            elif action == "start_sim_live":
                if active_replay_task and not active_replay_task.done():
                    active_replay_task.cancel()
                real_time_streamer.stop_stream()
                
                # --- [新增] 模擬實時看盤預載 150 根歷史數據 ---
                try:
                    conn = get_db_connection()
                    # 載入最近 2500 筆 1K 數據 (約為 2 天完整交易日數據)
                    query = "SELECT ts, Open as open, High as high, Low as low, Close as close, Volume as volume FROM futures1k WHERE code='TXFR1' ORDER BY ts DESC LIMIT 2500;"
                    df_hist = pd.read_sql_query(query, conn)
                    conn.close()
                    
                    if not df_hist.empty:
                        df_hist = df_hist.iloc[::-1].copy().reset_index(drop=True)
                        df_hist['datetime'] = pd.to_datetime(df_hist['ts'])
                        df_hist = df_hist.drop(columns=['ts'])
                        
                        history_payload = await get_history_init_payload(df_hist, **real_time_streamer.orb_params)
                        await websocket.send_json(history_payload)
                        logger.info("Sent history_init payload to simulated live terminal successfully.")
                except Exception as e:
                    logger.exception("Error sending history init for start_sim_live")
                    
                active_replay_task = asyncio.create_task(
                    simulated_live_ticks_worker()
                )
                await websocket.send_json({
                    "type": "status",
                    "message": "模擬實時看盤已啟動，每秒鐘動態更新價格。"
                })
                
            elif action == "update_orb_params":
                new_params = data.get("params", {})
                real_time_streamer.orb_params.update(new_params)
                
                # 重新獲取並推送 history_init 包以讓前端重繪
                try:
                    conn = get_db_connection()
                    query = "SELECT ts, Open as open, High as high, Low as low, Close as close, Volume as volume FROM futures1k WHERE code='TXFR1' ORDER BY ts DESC LIMIT 2500;"
                    df_hist = pd.read_sql_query(query, conn)
                    conn.close()
                    
                    if not df_hist.empty:
                        df_hist = df_hist.iloc[::-1].copy().reset_index(drop=True)
                        df_hist['datetime'] = pd.to_datetime(df_hist['ts'])
                        df_hist = df_hist.drop(columns=['ts'])
                        df_hist['session'] = df_hist['datetime'].apply(get_session)
                        
                        history_payload = await get_history_init_payload(df_hist, **real_time_streamer.orb_params)
                        await websocket.send_json(history_payload)
                        logger.info("Successfully updated ORB params and sent updated history_init payload.")
                except Exception as e:
                    logger.exception("Error updating ORB params and sending history init")
                
            elif action == "start_real_live":
                if active_replay_task and not active_replay_task.done():
                    active_replay_task.cancel()
                
                # 檢查 Shioaji Client 是否登入
                if not hasattr(app.state, 'api') or app.state.api is None:
                     await websocket.send_json({
                          "type": "error",
                          "message": "❌ Shioaji API 未登入！請在環境變數或 .env 中設置 SHIOAJI_API_KEY 與 SHIOAJI_SECRET_KEY 並重啟服務以啟用實盤行情。"
                     })
                     continue
                
                success = real_time_streamer.start_stream(app.state.api)
                if success:
                     # --- [新增] 真實實盤串流預載 150 根歷史數據 ---
                     try:
                          if not real_time_streamer.df_1k_real.empty:
                              history_payload = await get_history_init_payload(real_time_streamer.df_1k_real, **real_time_streamer.orb_params)
                              await websocket.send_json(history_payload)
                              logger.info("Sent history_init payload to real live terminal successfully.")
                     except Exception as e:
                         logger.exception("Error sending history init for start_real_live")
                         
                     await websocket.send_json({
                          "type": "status",
                          "message": "🟢 成功串接 Shioaji 台指期 (TXFR1) 實時串流行情！等待 Tick 推送中..."
                     })
                else:
                     await websocket.send_json({
                          "type": "error",
                          "message": "❌ 啟動 Shioaji 即時行情訂閱失敗，請檢查 API 連線或合約狀態。"
                     })
                     
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("WebSocket connection disconnected.")
        # 如果沒有活躍的連接，主動關閉 Shioaji 訂閱以釋放流量
        if len(manager.active_connections) == 0:
             real_time_streamer.stop_stream()
             if active_replay_task and not active_replay_task.done():
                  active_replay_task.cancel()
    except Exception as e:
        logger.exception("WebSocket error")
        manager.disconnect(websocket)
        if len(manager.active_connections) == 0:
             real_time_streamer.stop_stream()
             if active_replay_task and not active_replay_task.done():
                  active_replay_task.cancel()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


