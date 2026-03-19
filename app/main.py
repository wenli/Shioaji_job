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

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Lifespan for Shioaji Init & Scheduler Startup
@asynccontextmanager
async def lifespan(app: FastAPI):
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
