"""Basit ama gerçekçi walk-forward backtest."""
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd

from .features import indikatorler, FEATURE_COLS
from .model import _make_model

logger = logging.getLogger("bist-bot.backtest")


def run_backtest(HIST: Dict[str, pd.DataFrame], cfg: dict) -> str:
    if len(HIST) < 20:
        return "📊 Backtest için yeterli veri yok."

    risk = cfg.get("risk", {})
    stop_pct_fixed = 0.05
    hedef_pct = 0.08
    horizon = cfg.get("model", {}).get("prediction_horizon", 5)

    kayitlar = []  # (kod, sonuc, ret, tarih)

    # Son 12 ay, ay başı noktaları
    end = datetime.utcnow() + timedelta(hours=3) - timedelta(days=5)
    aylar = pd.date_range(end=end, periods=12, freq="ME")

    for kod, df0 in list(HIST.items())[:70]:
        if kod in ("XU100", "USDTRY", "SPX") or len(df0) < 280:
            continue
        try:
            df = indikatorler(df0).dropna()
            use_cols = [c for c in FEATURE_COLS if c in df.columns]
            son_model = None

            for e in aylar:
                train = df[df.index < e]
                if len(train) < 200:
                    continue

                # Her çeyrekte modeli yenile
                if son_model is None or e.month in (1, 4, 7, 10):
                    tr = train.copy()
                    tr["Hedef"] = (tr["Close"].shift(-horizon) / tr["Close"] - 1).clip(-0.5, 0.5)
                    tr = tr.dropna(subset=["Hedef"])
                    if len(tr) < 150:
                        continue
                    m = _make_model(cfg)
                    m.fit(tr[use_cols], tr["Hedef"])
                    son_model = m

                last = train.iloc[[-1]]
                pred = float(son_model.predict(last[use_cols])[0])
                rsi = float(last["RSI"].iloc[0])
                if pred <= 0 or rsi > 75:
                    continue

                alis = float(last["Close"].iloc[0])
                fut = df[df.index > e].head(horizon + 2)
                if len(fut) < horizon:
                    continue

                low_min = float(fut["Low"].min())
                high_max = float(fut["High"].max())
                close_end = float(fut["Close"].iloc[-1])

                if low_min <= alis * (1 - stop_pct_fixed):
                    kayitlar.append((kod, "L", -stop_pct_fixed, e))
                elif high_max >= alis * (1 + hedef_pct):
                    kayitlar.append((kod, "W", hedef_pct, e))
                else:
                    ret = close_end / alis - 1
                    kayitlar.append((kod, "F", ret, e))
        except Exception as e:
            logger.debug(f"BT {kod}: {e}")
            continue

    if not kayitlar:
        return "📊 Backtest sinyal üretmedi."

    w = sum(1 for k in kayitlar if k[1] == "W")
    l = sum(1 for k in kayitlar if k[1] == "L")
    f = sum(1 for k in kayitlar if k[1] == "F")
    n = len(kayitlar)
    rets = [k[2] for k in kayitlar]
    avg = np.mean(rets) * 100
    total = np.sum(rets) * 100
    winrate = w / n * 100
    # Basit profit factor
    gains = sum(r for r in rets if r > 0)
    losses = abs(sum(r for r in rets if r < 0)) or 1e-9
    pf = gains / losses

    mesaj = (
        f"📊 <b>HAFTALIK / AYLIK KARNE (v2)</b>\n"
        f"🔎 {n} sinyal | ✅ {w} | 🛑 {l} | ⏸ {f}\n"
        f"🏆 Winrate: %{winrate:.0f}\n"
        f"📈 Ort. getiri: %{avg:.2f}\n"
        f"💰 Kümülatif (basit): %{total:.1f}\n"
        f"⚖️ Profit Factor: {pf:.2f}\n"
        f"⚠️ <i>Geçmiş performans gelecek garantisi değildir. Slippage/komisyon yok.</i>"
    )
    return mesaj
