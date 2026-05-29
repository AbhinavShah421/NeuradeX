"""Compute all technical indicators from a candle DataFrame."""

from typing import Optional
import pandas as pd
import numpy as np


def compute_indicators(df: pd.DataFrame) -> dict:
    """
    df must have columns: open, high, low, close, volume (sorted oldest→newest).
    Returns a flat dict of indicator values for the LAST candle.
    """
    if df is None or len(df) < 2:
        return {}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    result: dict = {}

    # Price
    result["close"] = float(close.iloc[-1])
    result["open"] = float(df["open"].iloc[-1])
    result["high"] = float(high.iloc[-1])
    result["low"] = float(low.iloc[-1])
    result["volume"] = int(volume.iloc[-1])

    # --- Moving Averages ---
    for period in [9, 20, 50, 200]:
        if len(close) >= period:
            result[f"ema_{period}"] = float(close.ewm(span=period, adjust=False).mean().iloc[-1])
            result[f"sma_{period}"] = float(close.rolling(period).mean().iloc[-1])

    # --- RSI ---
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        result["rsi_14"] = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0

    # --- MACD ---
    if len(close) >= 27:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        result["macd"] = float(macd_line.iloc[-1])
        result["macd_signal"] = float(signal_line.iloc[-1])
        result["macd_hist"] = float((macd_line - signal_line).iloc[-1])

    # --- Bollinger Bands ---
    if len(close) >= 20:
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        result["bb_upper"] = float(bb_upper.iloc[-1])
        result["bb_lower"] = float(bb_lower.iloc[-1])
        bb_range = float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1])
        result["bb_position"] = (
            (result["close"] - float(bb_lower.iloc[-1])) / bb_range
        ) if bb_range > 0 else 0.5
        result["bb_width"] = bb_range / float(sma20.iloc[-1]) if float(sma20.iloc[-1]) > 0 else 0

    # --- ATR ---
    if len(df) >= 15:
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        result["atr_14"] = float(tr.rolling(14).mean().iloc[-1])

    # --- VWAP (daily approximation) ---
    if len(df) >= 5:
        typical = (high + low + close) / 3
        vwap = (typical * volume).cumsum() / volume.cumsum()
        result["vwap"] = float(vwap.iloc[-1])
        result["vwap_distance_pct"] = (result["close"] - result["vwap"]) / result["vwap"] * 100

    # --- Volume SMA ---
    if len(volume) >= 20:
        vol_sma = volume.rolling(20).mean()
        result["volume_sma_20"] = float(vol_sma.iloc[-1])
        result["volume_ratio"] = float(volume.iloc[-1]) / float(vol_sma.iloc[-1]) if float(vol_sma.iloc[-1]) > 0 else 1.0

    # --- Stochastic ---
    if len(df) >= 14:
        low14 = low.rolling(14).min()
        high14 = high.rolling(14).max()
        stoch_k = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
        result["stoch_k"] = float(stoch_k.iloc[-1]) if not np.isnan(stoch_k.iloc[-1]) else 50.0
        result["stoch_d"] = float(stoch_k.rolling(3).mean().iloc[-1]) if not np.isnan(stoch_k.rolling(3).mean().iloc[-1]) else 50.0

    # --- Williams %R ---
    if len(df) >= 14:
        low14 = low.rolling(14).min()
        high14 = high.rolling(14).max()
        wr = -100 * (high14 - close) / (high14 - low14).replace(0, np.nan)
        result["williams_r"] = float(wr.iloc[-1]) if not np.isnan(wr.iloc[-1]) else -50.0

    # --- Support / Resistance (simple pivot) ---
    if len(df) >= 2:
        prev = df.iloc[-2]
        pivot = (float(prev["high"]) + float(prev["low"]) + float(prev["close"])) / 3
        result["pivot"] = pivot
        result["r1"] = 2 * pivot - float(prev["low"])
        result["s1"] = 2 * pivot - float(prev["high"])

    return result


def build_feature_vector(indicators: dict) -> list[float]:
    """Build ordered feature vector for ML model inference."""
    keys = [
        "rsi_14", "macd_hist", "bb_position", "bb_width",
        "stoch_k", "stoch_d", "williams_r", "volume_ratio",
        "vwap_distance_pct", "atr_14",
    ]
    ema_keys = ["ema_9", "ema_20", "ema_50", "ema_200"]
    close = indicators.get("close", 1.0)
    features = []
    for k in keys:
        features.append(float(indicators.get(k, 0.0)))
    for ek in ema_keys:
        ema = indicators.get(ek)
        if ema and close > 0:
            features.append((close - ema) / close * 100)
        else:
            features.append(0.0)
    return features
