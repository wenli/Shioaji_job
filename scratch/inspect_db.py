import sqlite3
import pandas as pd
from app.strategy.sweep_fade import SweepFadeConfig, SweepFadeEngine

con = sqlite3.connect('Shioaji.db')
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Tables:", cur.fetchall())

for table in ['kbars_tx_1m', 'kbars_mtx_1m', 'kbars_tx_5m', 'kbars_mtx_5m', 'kbars_1m', 'kbars_5m']:
    try:
        cur.execute(f"SELECT min(datetime), max(datetime), count(*) FROM {table}")
        print(table, cur.fetchone())
    except Exception as e:
        pass
