import sqlite3
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Load Environment Variables
load_dotenv()
DB_NAME = os.getenv("DB_NAME", "Shioaji.db")

def get_db_connection() -> sqlite3.Connection:
    """Returns a SQLite connection with timeout for busy handling.

    Returns:
        sqlite3.Connection: Database connection.
    """
    db_path = Path(DB_NAME)
    # Ensure parent dir exists if relative or absolute contains slashes
    if db_path.parent != Path("."):
         db_path.parent.mkdir(parents=True, exist_ok=True)
         
    conn = sqlite3.connect(DB_NAME, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Initializes the SQLite database creating required tables for K-lines.
    
    Creates separate tables for 1k, 5k, 15k, 30k, 60k, 1d with PK(code, ts).
    """
    tables = [
        "futures1k", "futures5k", "futures15k",
        "futures30k", "futures60k", "futures1d"
    ]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        for table in tables:
            logger.info(f"Checking/Creating table: {table}")
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    code TEXT,
                    ts TIMESTAMP,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    PRIMARY KEY (code, ts)
                )
            """)
        conn.commit()
        logger.info("Database initialization successful.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_last_ts(table: str, code: str) -> str:
    """Gets the latest timestamp for a contract in a table.

    Args:
        table (str): Target table name (e.g., futures1k).
        code (str): Contract code (e.g., TXFR1).

    Returns:
        str: Maximum timestamp found, or "" if none.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT MAX(ts) FROM {table} WHERE code = ?", (code,))
        result = cursor.fetchone()
        if result and result[0] is not None:
            return str(result[0])
        return ""
    except Exception as e:
        logger.error(f"Error getting last ts from {table}: {e}")
        return ""
    finally:
        conn.close()

import pandas as pd

def save_to_db(df: pd.DataFrame, table_name: str) -> int:
    """Saves DataFrame to specified SQLite table.

    Args:
        df (pd.DataFrame): Data to save, must have code, ts, open, high, low, close, volume.
        table_name (str): Target table name.

    Returns:
        int: Number of rows inserted.
    """
    if df.empty:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Prepare rows for executemany
    # dataframe order: code, ts, open, high, low, close, volume
    rows = list(df[['code', 'ts', 'open', 'high', 'low', 'close', 'volume']].itertuples(index=False, name=None))

    try:
        cursor.executemany(f"""
            INSERT OR IGNORE INTO {table_name} 
            (code, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        inserted = cursor.rowcount
        # rowcount may be -1 for some drivers, but sqlite executemany returns total if supported
        # or we can check difference in count. Let's return just positive indicators.
        return inserted if inserted >= 0 else len(rows)
    except Exception as e:
        logger.error(f"Error saving to {table_name}: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()

def aggregate_kbars(df_1k: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Aggregates 1k K-bars into higher timeframes.

    Args:
        df_1k (pd.DataFrame): 1k data must have 'ts_datetime' as index and columns: open, high, low, close, volume, code.
        interval (str): Pandas resample rule (e.g., '5min', '15min', '60min', 'D').

    Returns:
        pd.DataFrame: Aggregated K-bars.
    """
    if df_1k.empty:
        return pd.DataFrame()

    # Define aggregation rules
    # Note: open: first, high: max, low: min, close: last, volume: sum
    agg_rules = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }

    # Resample
    # label='left' or 'right' depending on standard. Typically Taiwan market uses closed='left', label='left' or 'right'.
    # e.g., 08:45-08:50 labeled 08:45 OR 08:50. 
    # Let's use closed='left', label='left' for standard aggregation.
    resampled = df_1k.resample(interval, closed='left', label='left').agg(agg_rules)
    
    # Drop NaNs that occur in non-trading hours
    resampled.dropna(subset=['open'], inplace=True)
    
    # Restore code and ts (nanosecs for timestamp integer)
    if not df_1k.empty:
         resampled['code'] = df_1k['code'].iloc[0]
         
    resampled.reset_index(inplace=True)
    # Rename and convert to readable TIMESTAMP string
    resampled.rename(columns={'ts_datetime': 'ts'}, inplace=True)
    resampled['ts'] = resampled['ts'].dt.strftime('%Y-%m-%d %H:%M:%S')
    
    return resampled

def download_kbars(api, contract, start_date: str, end_date: str) -> dict:
    """Downloads 1k K-bars, aggregates to higher timeframes, and saves all to DB.

    Args:
        api (shioaji.Shioaji): Logged in Shioaji client.
        contract (shioaji.contracts.Contract): Targeted contract object.
        start_date (str): Start date in YYYY-MM-DD.
        end_date (str): End date in YYYY-MM-DD.

    Returns:
        dict: Summary of inserted row counts per interval.
    """
    code = contract.code
    logger.info(f"Downloading 1k K-bars for {code} from {start_date} to {end_date}...")

    try:
        kbars = api.kbars(contract=contract, start=start_date, end=end_date)
        
        if not kbars or not hasattr(kbars, 'ts') or len(kbars.ts) == 0:
            logger.warning(f"No data returned for {code}.")
            return {}

        # 1. Create 1k DataFrame
        # Map Shioaji capitalized keys to lowercase for DB compatibility
        df_1k = pd.DataFrame({
            'ts': kbars.ts,
            'open': kbars.Open,
            'high': kbars.High,
            'low': kbars.Low,
            'close': kbars.Close,
            'volume': kbars.Volume
        })
        df_1k['code'] = code

        # Convert ts to datetime index for resampling
        df_1k['ts_datetime'] = pd.to_datetime(df_1k['ts'], unit='ns')
        df_1k.set_index('ts_datetime', inplace=True)

        # 2. Save 1k directly
        stats = {}
        df_1k_save = df_1k.copy()
        df_1k_save.reset_index(inplace=True)
        df_1k_save['ts'] = df_1k_save['ts_datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')
        inserted_1k = save_to_db(df_1k_save, "futures1k")
        stats["1k"] = inserted_1k
        
        # 3. Aggregate and Save others
        intervals = {
            "5k": "5min",
            "15k": "15min",
            "30k": "30min",
            "60k": "60min",
            "1d": "D"
        }

        for name, rule in intervals.items():
            logger.info(f"Aggregating to {name}...")
            df_agg = aggregate_kbars(df_1k, rule)
            inserted = save_to_db(df_agg, f"futures{name}")
            stats[name] = inserted

        logger.info(f"Sync Stats for {code}: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Download/Aggregation pipeline failed for {code}: {e}")
        return {}

import shioaji as sj

def get_shioaji_client():
    """Initializes and logs into Shioaji client using .env credentials.

    Returns:
        shioaji.Shioaji: Logged in client, or None if failed.
    """
    api_key = os.getenv("SHIOAJI_API_KEY")
    secret_key = os.getenv("SHIOAJI_SECRET_KEY")
    simulation = os.getenv("SHIOAJI_SIMULATION", "True") == "True"

    if not api_key or not secret_key:
        logger.error("SHIOAJI_API_KEY or SHIOAJI_SECRET_KEY missing in .env")
        return None

    try:
        logger.info(f"Initializing Shioaji (Simulation={simulation})...")
        api = sj.Shioaji(simulation=simulation)
        api.login(api_key, secret_key)
        logger.info("Shioaji Login Successful.")
        return api
    except Exception as e:
        logger.error(f"Shioaji Login Failed: {e}")
        return None

def sync_to_latest(api, contract) -> dict:
    """Synchronizes K-lines from the last known timestamp up to today.

    Args:
        api (shioaji.Shioaji): Logged in Shioaji client.
        contract (shioaji.contracts.Contract): Targeted contract.

    Returns:
        dict: Sync statistics.
    """
    code = contract.code
    logger.info(f"Checking sync breakpoint for {code}...")
    
    last_ts = get_last_ts("futures1k", code)
    if not last_ts:
        # Default start date if no data exists. 
        # Shioaji futures history starts 2020-03-22
        # Let's default to a safe recent range or full backfill if requested.
        # Here we'll default to 2024-01-01 to avoid massive downloads unless intended
        start_date = os.getenv("DEFAULT_START_DATE", "2024-01-01")
        logger.info(f"No existing data for {code}. Starting full sync from {start_date}")
    else:
        # Convert last_ts to explicitly starting day string
        # Safe to overlap because of INSERT OR IGNORE
        start_date = pd.to_datetime(last_ts).strftime('%Y-%m-%d')
        logger.info(f"Found last TS: {last_ts} ({start_date}). Syncing onward.")

    end_date = pd.Timestamp.now().strftime('%Y-%m-%d')
    
    return download_kbars(api, contract, start_date, end_date)

if __name__ == "__main__":
    logger.info("Starting Database Initialization...")
    init_db()
    logger.info("Finished.")
