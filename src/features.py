"""Teknik indikatörler ve feature mühendisliği."""
from typing import Optional
import pandas as pd
import numpy as np


def indikatorler(df: pd.DataFrame) -> pd.DataFrame:
    """Temel teknik indikatörleri hesaplar."""
    df = df.copy()
    c = df["Close"]
    h, l, v = df["High"], df["Low"], df["Volume"]

    # Moving averages
    df["SMA20"] = c.rolling(20).mean()
    df["SMA50"] = c.rolling(50).mean()
    df["EMA12"] = c.ewm(span=12, adjust=False).mean()
    df["EMA26"] = c.ewm(span=26, adjust=False).mean()

    # RSI
    delta = c.diff()
    ag = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
    al = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
    df["RSI"] = 100 - (100 / (1 + ag / al.replace(0, 1e-10)))

    # MACD
    df["MACD"] = df["EMA12"] - df["EMA26"]
    df["MACDs"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACDh"] = df["MACD"] - df["MACDs"]

    # Bollinger
    std = c.rolling(20).std()
    df["BBU"] = df["SMA20"] + 2 * std
    df["BBL"] = df["SMA20"] - 2 * std
    df["BB_width"] = (df["BBU"] - df["BBL"]) / df["SMA20"]
    df["BB_pos"] = (c - df["BBL"]) / (df["BBU"] - df["BBL"] + 1e-10)

    # ATR
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1/14, min_periods=14).mean()
    df["ATR_pct"] = df["ATR"] / c * 100

    # Momentum & returns
    df["ret_1"] = c.pct_change(1)
    df["ret_5"] = c.pct_change(5)
    df["ret_20"] = c.pct_change(20)
    df["vol_20"] = df["ret_1"].rolling(20).std() * np.sqrt(252)

    # Volume features
    df["vol_sma20"] = v.rolling(20).mean()
    df["rel_vol"] = v / (df["vol_sma20"] + 1)

    # Trend strength
    df["above_sma20"] = (c > df["SMA20"]).astype(int)
    df["above_sma50"] = (c > df["SMA50"]).astype(int)
    df["sma_cross"] = (df["SMA20"] > df["SMA50"]).astype(int)

    # 52w proximity (approximate from available data)
    df["hi_52"] = c.rolling(252, min_periods=60).max()
    df["lo_52"] = c.rolling(252, min_periods=60).min()
    df["pct_from_hi"] = (c / df["hi_52"] - 1) * 100
    df["pct_from_lo"] = (c / df["lo_52"] - 1) * 100

    return df


FEATURE_COLS = [
    "Close", "Volume", "SMA20", "SMA50", "RSI", "MACD", "MACDs", "MACDh",
    "BBU", "BBL", "BB_width", "BB_pos", "ATR", "ATR_pct",
    "ret_1", "ret_5", "ret_20", "vol_20", "rel_vol",
    "above_sma20", "above_sma50", "sma_cross",
    "pct_from_hi", "pct_from_lo"
]


def skor_teknik(a: dict) -> Optional[dict]:
    """TradingView alanlarından basit teknik skor (ML yoksa fallback)."""
    c = a.get("close")
    if not c:
        return None
    s = 0.0
    rsi = a.get("rsi") or 50
    if rsi < 30:
        s += 2.5
    elif rsi <= 45:
        s += 2.0
    elif rsi <= 60:
        s += 1.0
    elif rsi > 70:
        s -= 2.0

    if a.get("macd") is not None and a.get("sig") is not None and a["macd"] > a["sig"]:
        s += 2.0
    if a.get("sma20") and c > a["sma20"]:
        s += 1.0
    if a.get("perfw") is not None and -5 <= a["perfw"] <= 3:
        s += 1.0
    if a.get("relvol") is not None and a["relvol"] > 1.8:
        s += 1.5
    if a.get("rec") is not None and a["rec"] > 0:
        s += 1.0
    atrp = (a["atr"] / c * 100) if a.get("atr") else 2.0
    return {"skor": s, "rsi": rsi, "close": c, "atrp": atrp}
