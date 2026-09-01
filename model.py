"""ML model eğitimi ve tahmin."""
import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .features import indikatorler, FEATURE_COLS

logger = logging.getLogger("bist-bot.model")

HAS_LGB = False
HAS_SKLEARN = False

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    pass

try:
    from sklearn.ensemble import GradientBoostingRegressor
    HAS_SKLEARN = True
except ImportError:
    pass


def _make_model(cfg: dict):
    mcfg = cfg.get("model", {})
    if HAS_LGB:
        return lgb.LGBMRegressor(
            n_estimators=mcfg.get("n_estimators", 150),
            max_depth=mcfg.get("max_depth", 4),
            learning_rate=mcfg.get("learning_rate", 0.05),
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=-1,
            n_jobs=2
        )
    elif HAS_SKLEARN:
        return GradientBoostingRegressor(
            n_estimators=mcfg.get("n_estimators", 100),
            max_depth=mcfg.get("max_depth", 3),
            learning_rate=mcfg.get("learning_rate", 0.05),
            random_state=42
        )
    else:
        raise ImportError("Ne lightgbm ne scikit-learn yüklü değil. requirements.txt'i kurun.")


def analiz_ml(ticker: str, guncel: Dict[str, float], HIST: Dict[str, pd.DataFrame],
              cfg: dict) -> Optional[dict]:
    """
    Hisse için 5-günlük getiri tahmini yapar.
    Döner: fiyat, tahmin (%), rsi, stop, hedef, sektör vb.
    """
    from .config_loader import sektor_of

    try:
        df = HIST.get(ticker)
        min_rows = cfg.get("model", {}).get("min_train_rows", 120)
        if df is None or len(df) < min_rows:
            return None

        df = df.copy()
        son_fiyat = guncel.get(ticker) or float(df["Close"].iloc[-1])
        ys = float(df["Close"].iloc[-1])
        oran = son_fiyat / ys if ys > 0 else 0
        # Aşırı sapma kontrolü (veri hatası)
        if not (0.4 < oran < 2.5):
            return None

        # Son fiyatı tarihsel seriye hizala
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = df[col] * oran

        df = indikatorler(df).dropna()
        if len(df) < 80:
            return None

        horizon = cfg.get("model", {}).get("prediction_horizon", 5)
        df["Hedef"] = (df["Close"].shift(-horizon) / df["Close"] - 1).clip(-0.5, 0.5)
        df = df.dropna(subset=["Hedef"])

        # Eksik feature'ları at
        use_cols = [c for c in FEATURE_COLS if c in df.columns]
        X = df[use_cols]
        y = df["Hedef"]

        model = _make_model(cfg)
        model.fit(X, y)

        pred = float(model.predict(X.iloc[[-1]])[0]) * 100  # %

        atr = float(df["ATR"].iloc[-1])
        risk_cfg = cfg.get("risk", {})
        stop_pct = min(
            max(risk_cfg.get("stop_atr_carpan", 2.0) * atr / son_fiyat,
                risk_cfg.get("stop_min_pct", 0.03)),
            risk_cfg.get("stop_max_pct", 0.08)
        )
        rr = risk_cfg.get("hedef_risk_odul", 1.8)

        return {
            "hisse": ticker,
            "fiyat": son_fiyat,
            "tahmin": pred,
            "rsi": float(df["RSI"].iloc[-1]),
            "atr": atr,
            "stop": round(son_fiyat * (1 - stop_pct), 2),
            "hedef": round(son_fiyat * (1 + rr * stop_pct), 2),
            "sektor": sektor_of(ticker, cfg),
            "motor": "LGBM" if HAS_LGB else "GBM"
        }
    except Exception as e:
        logger.debug(f"ML {ticker}: {type(e).__name__}: {e}")
        return None
