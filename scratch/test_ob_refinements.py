import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

# Let's test modifying order_block.py logic
# 1. Update origin_target dynamically with new highs
# 2. SL based on 1K confirmation candle low/high + 3 pts (tight SL = huge R:R!)
# 3. TP1 = 2.0R, TP2 = 4.0R or Opposite Pool

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

print("Data loaded successfully.")
