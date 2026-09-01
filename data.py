"""Veri çekme ve tarihsel veri yönetimi."""
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

logger = logging.getLogger("bist-bot.data")

KOLONLAR_EXT = [
    "close", "volume", "RSI", "MACD.macd", "MACD.signal",
    "simple_ma(20)", "ATR", "Perf.W", "relative_volume_10d_calc",
    "high_52w", "low_52w", "market_cap_basic", "recommend_all", "Perf.M"
]
KOLONLAR_BASIC = KOLONLAR_EXT[:8]


def trt_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=3)


def tv_tarama(tickers: List[str], kolonlar: List[str], timeout: int = 30) -> Dict[str, list]:
    """TradingView scanner'dan veri çeker."""
    for yol in ["turkey", "global"]:
        try:
            payload = {
                "symbols": {"tickers": tickers, "query": {"types": []}},
                "columns": kolonlar
            }
            r = requests.post(
                f"https://scanner.tradingview.com/{yol}/scan",
                json=payload,
                timeout=timeout
            )
            if r.status_code != 200:
                logger.warning(f"TV {yol} status {r.status_code}")
                continue
            out = {}
            for it in r.json().get("data", []):
                k = str(it.get("s", "")).split(":")[-1]
                d = it.get("d") or []
                if k and d and d[0] is not None:
                    out[k] = (list(d) + [None] * 14)[:14]
            if out:
                logger.info(f"TV {yol}: {len(out)} hisse")
                return out
        except Exception as e:
            logger.warning(f"TV hata ({yol}): {e}")
    return {}


def alanlar(d: list) -> dict:
    d = (list(d) + [None] * 14)[:14]
    return {
        "close": d[0], "vol": d[1], "rsi": d[2], "macd": d[3], "sig": d[4],
        "sma20": d[5], "atr": d[6], "perfw": d[7], "relvol": d[8],
        "hi52": d[9], "lo52": d[10], "mcap": d[11], "rec": d[12], "perfm": d[13]
    }


def csv_yukle(csv_path: str) -> Dict[str, pd.DataFrame]:
    """Tarihsel OHLCV verisini yükler."""
    try:
        df = pd.read_csv(csv_path, parse_dates=["Date"])
        out = {}
        for k, g in df.groupby("Ticker"):
            g = g.drop(columns=["Ticker"], errors="ignore")
            g = g.set_index("Date").sort_index()
            g = g.apply(pd.to_numeric, errors="coerce").dropna()
            # Aynı gün tekrarlarını temizle
            g = g[~g.index.duplicated(keep="last")]
            if len(g) > 60:
                out[k] = g
        logger.info(f"CSV yüklendi: {len(out)} ticker")
        return out
    except Exception as e:
        logger.error(f"CSV yükleme hatası: {e}")
        return {}


def csv_guncelle_tv(csv_path: str, ham: Dict[str, list], bugun: datetime):
    """TradingView'dan gelen close/volume ile basit güncelleme (yedek)."""
    try:
        bstr = bugun.strftime("%Y-%m-%d")
        df0 = pd.read_csv(csv_path)
        sonl = df0.groupby("Ticker")["Date"].max().astype(str).to_dict()
        yeni = []
        for k, v in ham.items():
            if sonl.get(k, "") < bstr:
                close = v[0]
                vol = v[1] if v[1] else 1e6
                yeni.append({
                    "Date": bstr,
                    "Open": close, "High": close, "Low": close, "Close": close,
                    "Volume": vol, "Ticker": k
                })
        if yeni:
            pd.DataFrame(yeni).to_csv(csv_path, mode="a", header=False, index=False)
            logger.info(f"CSV TV güncelleme +{len(yeni)} satır")
    except Exception as e:
        logger.warning(f"CSV TV güncelleme: {e}")


def yfinance_ohlcv_guncelle(tickers: List[str], csv_path: str, cfg: dict):
    """
    yfinance ile gerçek OHLCV çeker ve CSV'yi zenginleştirir.
    Sadece eksik / eski günleri ekler, mevcut gerçek OHLCV'yi bozmaz.
    """
    data_cfg = cfg.get("data", {})
    if not data_cfg.get("yfinance_guncelle", False):
        return

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance yüklü değil, OHLCV güncellemesi atlandı")
        return

    max_h = data_cfg.get("yfinance_max_hisse", 40)
    period = data_cfg.get("yfinance_period", "1y")
    existing = csv_yukle(csv_path)
    yeni_kayitlar = []
    basarili = 0

    for t in tickers[:max_h]:
        try:
            df = yf.download(
                f"{t}.IS",
                period=period,
                progress=False,
                auto_adjust=True,
                threads=False
            )
            if df is None or df.empty or len(df) < 30:
                continue

            # MultiIndex kolon temizliği (yeni yf sürümleri)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            df = df.reset_index()
            # Date kolonunu standartlaştır
            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df = df.rename(columns={date_col: "Date"})
            df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
            df["Ticker"] = t

            need = ["Date", "Open", "High", "Low", "Close", "Volume", "Ticker"]
            for col in need:
                if col not in df.columns:
                    if col == "Volume":
                        df["Volume"] = 0
                    else:
                        continue
            df = df[need].dropna(subset=["Close"])

            if t in existing:
                last = existing[t].index.max()
                # Sadece daha yeni günleri al
                df = df[df["Date"] > pd.Timestamp(last)]
            if not df.empty:
                yeni_kayitlar.append(df)
                basarili += 1
            time.sleep(0.35)
        except Exception as e:
            logger.debug(f"yf {t}: {e}")

    if yeni_kayitlar:
        full = pd.concat(yeni_kayitlar, ignore_index=True)
        # Mevcut CSV'deki aynı gün+ticker kayıtlarını ezmemek için append
        full.to_csv(csv_path, mode="a", header=False, index=False)
        logger.info(f"yfinance OHLCV: {basarili} hisse, +{len(full)} satır eklendi")
    else:
        logger.info("yfinance: eklenecek yeni gün yok")


def haber_bayrak(kod: str) -> Tuple[int, str]:
    """Son 48 saatteki Google News sayısı ve ilk başlık."""
    try:
        url = f"https://news.google.com/rss/search?q={kod}+borsa&hl=tr&gl=TR&ceid=TR:tr"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        root = ET.fromstring(r.text.encode("utf-8"))
        now = datetime.now(timezone.utc)
        n, baslik = 0, ""
        for it in root.findall(".//item")[:12]:
            try:
                dt = parsedate_to_datetime(it.findtext("pubDate"))
                if (now - dt).total_seconds() < 48 * 3600:
                    n += 1
                    if not baslik:
                        baslik = (it.findtext("title") or "").split(" - ")[0][:70]
            except Exception:
                pass
        return n, baslik
    except Exception:
        return 0, ""


def korelasyon_matrisi(HIST: Dict[str, pd.DataFrame], tickers: List[str],
                       lookback: int = 60) -> pd.DataFrame:
    """Son N günün close getiri korelasyon matrisi."""
    rets = {}
    for t in tickers:
        df = HIST.get(t)
        if df is None or len(df) < lookback + 5:
            continue
        r = df["Close"].pct_change().dropna().tail(lookback)
        if len(r) >= lookback // 2:
            rets[t] = r
    if len(rets) < 2:
        return pd.DataFrame()
    return pd.DataFrame(rets).corr()
