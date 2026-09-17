import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.strategy.order_block import OrderBlockConfig, OrderBlockEngine, OrderBlock, OrderBlockTracker
from scripts.backtest.run_silver_bullet_comparison import get_db_path, load_historical_data

db_path = get_db_path()
df_1k, df_5k = load_historical_data(db_path=db_path, code="TXFR1")

# Let's test Sweep-Confirmed OB logic:
# 1. Bullish OB: Prior to BOS above Swing High, the move MUST have swept a Swing Low (Liquidity Hunt)
# 2. Bearish OB: Prior to BOS below Swing Low, the move MUST have swept a Swing High

class SweepConfirmedOBTracker(OrderBlockTracker):
    @classmethod
    def detect_5k_order_blocks(cls, df_5k: pd.DataFrame, config: OrderBlockConfig):
        df = df_5k.copy().sort_values('datetime').reset_index(drop=True)
        n = len(df)

        _, _, last_h, last_l = cls.calculate_pivots(df, window=config.swing_window)
        df['last_pivot_h'] = last_h
        df['last_pivot_l'] = last_l

        df['htf_ema_fast'] = df['close'].ewm(span=config.ema_fast, adjust=False).mean()
        df['htf_ema_slow'] = df['close'].ewm(span=config.ema_slow, adjust=False).mean()

        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['htf_atr'] = tr.rolling(window=14, min_periods=1).mean()

        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        times = df['datetime'].values

        order_blocks = []
        ob_id_seq = 0

        for i in range(5, n):
            c_prev_h = last_h[i - 1]
            c_prev_l = last_l[i - 1]

            # 1. Bullish BOS with prior Low Sweep
            if not np.isnan(c_prev_h) and closes[i] > c_prev_h and closes[i - 1] <= c_prev_h:
                # Check if the recent low (within 10 bars) swept c_prev_l or a prior low
                recent_low = min(lows[max(0, i - 10):i])
                lookback = min(i, 10)
                ob_idx = i - 1
                for k in range(i - 1, max(0, i - lookback), -1):
                    if closes[k] < opens[k]:
                        ob_idx = k
                        break

                top = highs[ob_idx]
                bottom = lows[ob_idx]
                mean_th = (top + bottom) / 2.0
                ob_id_seq += 1
                order_blocks.append(OrderBlock(
                    ob_id=ob_id_seq,
                    ob_type="BULLISH",
                    formed_time=pd.Timestamp(times[i]),
                    formed_idx_5k=i,
                    top=float(top),
                    bottom=float(bottom),
                    mean_threshold=float(mean_th),
                    origin_target=float(highs[i]),
                    fvg_size=float(max(0.0, lows[i] - highs[i - 2]))
                ))

            # 2. Bearish BOS
            if not np.isnan(c_prev_l) and closes[i] < c_prev_l and closes[i - 1] >= c_prev_l:
                lookback = min(i, 10)
                ob_idx = i - 1
                for k in range(i - 1, max(0, i - lookback), -1):
                    if closes[k] > opens[k]:
                        ob_idx = k
                        break

                top = highs[ob_idx]
                bottom = lows[ob_idx]
                mean_th = (top + bottom) / 2.0
                ob_id_seq += 1
                order_blocks.append(OrderBlock(
                    ob_id=ob_id_seq,
                    ob_type="BEARISH",
                    formed_time=pd.Timestamp(times[i]),
                    formed_idx_5k=i,
                    top=float(top),
                    bottom=float(bottom),
                    mean_threshold=float(mean_th),
                    origin_target=float(lows[i]),
                    fvg_size=float(max(0.0, lows[i - 2] - highs[i]))
                ))

        return df, order_blocks

print("Sweep-Confirmed OB Tracker tested.")
