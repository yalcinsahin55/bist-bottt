"""Pozisyon yönetimi, rejim tespiti, trailing, partial TP ve korelasyon filtresi."""
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging
import json
from pathlib import Path

import pandas as pd
import numpy as np

logger = logging.getLogger("bist-bot.risk")


def piyasa_rejimi(HIST: dict, tv_xu100: list = None) -> Tuple[str, bool]:
    """XU100 trendine göre rejim. Returns: (metin, firtina_modu)"""
    try:
        xi = HIST.get("XU100")
        if xi is not None and len(xi) > 60:
            c = float(xi["Close"].iloc[-1])
            s20 = float(xi["Close"].rolling(20).mean().iloc[-1])
            s50 = float(xi["Close"].rolling(50).mean().iloc[-1])
        elif tv_xu100 and len(tv_xu100) >= 3:
            c, s20, s50 = tv_xu100[0], tv_xu100[1], tv_xu100[2]
        else:
            return "⚪ Veri yok", False

        if c > s20 and c > s50:
            return "🟢 YÜKSELİŞ TRENDİ", False
        elif c > s20:
            return "🟡 KARARSIZ", False
        else:
            return "🔴 FIRTINA MODU — max 2 pozisyon", True
    except Exception:
        return "⚪", False


def makro_ozet(HIST: dict) -> str:
    def trend(k):
        xi = HIST.get(k)
        if xi is None or len(xi) < 60:
            return "⚪"
        c = float(xi["Close"].iloc[-1])
        s = float(xi["Close"].rolling(50).mean().iloc[-1])
        return "🟢" if c > s else "🔴"
    return f"🌍 BIST:{trend('XU100')} | USD/TL:{trend('USDTRY')} | S&P500:{trend('SPX')}"


def _kaydet_kapali_islem(trades_path: str, pos: dict, kapanis_fiyat: float,
                         neden: str, bugun: datetime):
    """Kapalı işlemi trades_history.json'a kaydeder."""
    try:
        trades = []
        p = Path(trades_path)
        if p.exists():
            trades = json.load(open(p, encoding="utf-8"))
        alis = pos["alis"]
        ret = (kapanis_fiyat - alis) / alis * 100
        gun = (bugun - datetime.fromisoformat(pos["tarih"])).days
        kayit = {
            "hisse": pos["hisse"],
            "alis": alis,
            "satis": round(kapanis_fiyat, 2),
            "getiri_pct": round(ret, 2),
            "gun": gun,
            "neden": neden,
            "alis_tarih": pos["tarih"],
            "satis_tarih": bugun.strftime("%Y-%m-%d"),
            "stop": pos.get("stop"),
            "hedef": pos.get("hedef"),
            "partial": pos.get("partial", False)
        }
        trades.append(kayit)
        # Son 500 işlemi tut
        trades = trades[-500:]
        json.dump(trades, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        logger.info(f"Kapalı işlem kaydedildi: {pos['hisse']} {ret:+.1f}% ({neden})")
    except Exception as e:
        logger.warning(f"Trade kaydetme hatası: {e}")


def pozisyonlari_guncelle(
    gecmis: List[dict],
    fiyatlar: Dict[str, float],
    guncel: Dict[str, float],
    bugun: datetime,
    cfg: dict,
    trades_path: str
) -> Tuple[List[str], List[dict]]:
    """
    Açık pozisyonları kontrol eder.
    - Stop / Hedef / Süre
    - Trailing stop
    - Partial take-profit
    Returns: (sat_mesajlari, kalan_acik_pozisyonlar)
    """
    risk = cfg.get("risk", {})
    max_gun = risk.get("max_tutma_gun", 10)
    trailing_aktif = risk.get("trailing_aktif", True)
    trail_act = risk.get("trailing_aktivasyon_pct", 0.04)
    trail_mesafe = risk.get("trailing_mesafe_pct", 0.025)
    partial_aktif = risk.get("partial_tp_aktif", True)
    partial_pct = risk.get("partial_tp_pct", 0.06)
    partial_oran = risk.get("partial_tp_oran", 0.5)

    satlar = []
    aciklar = []

    for pos in gecmis:
        kod = pos["hisse"]
        cur = fiyatlar.get(kod) or guncel.get(kod)
        if cur is None:
            pos["son"] = None
            aciklar.append(pos)
            continue

        pos["son"] = cur
        gun = (bugun - datetime.fromisoformat(pos["tarih"])).days
        kar_pct = (cur - pos["alis"]) / pos["alis"]

        # --- Partial Take Profit ---
        if (partial_aktif and not pos.get("partial") and kar_pct >= partial_pct):
            pos["partial"] = True
            # Stop'u en az alış seviyesine çek (breakeven)
            pos["stop"] = max(pos.get("stop", 0), round(pos["alis"] * 1.005, 2))
            satlar.append(
                f"💰 <b>{kod} PARTIAL TP</b> %{kar_pct*100:.1f} → "
                f"%{int(partial_oran*100)} sat, stop BE'ye çekildi"
            )
            # Not: Gerçek yarı satış simülasyonu için pozisyon boyutunu küçültmek gerekir;
            # burada sadece işaretliyoruz. Gerçek trading'de lot'u yarıya indirirsin.

        # --- Trailing Stop ---
        if trailing_aktif and kar_pct >= trail_act:
            yeni_stop = round(cur * (1 - trail_mesafe), 2)
            if yeni_stop > pos.get("stop", 0):
                eski = pos["stop"]
                pos["stop"] = yeni_stop
                # Sadece anlamlı değişikliklerde bildir
                if yeni_stop > eski * 1.01:
                    satlar.append(
                        f"🔼 <b>{kod} TRAILING</b> stop {eski} → {yeni_stop}"
                    )

        # --- Çıkış kontrolleri ---
        if cur >= pos["hedef"]:
            satlar.append(f"🎯 <b>{kod} HEDEF VURDU!</b> {cur:.2f} → KAR AL")
            _kaydet_kapali_islem(trades_path, pos, cur, "HEDEF", bugun)
        elif cur <= pos["stop"]:
            neden = "TRAILING_STOP" if pos.get("partial") or kar_pct > 0 else "STOP"
            satlar.append(f"🛑 <b>{kod} STOP VURDU!</b> {cur:.2f} → ZARAR KES" if kar_pct < 0
                          else f"🛑 <b>{kod} TRAILING STOP</b> {cur:.2f} → KAPAT")
            _kaydet_kapali_islem(trades_path, pos, cur, neden, bugun)
        elif gun >= max_gun:
            satlar.append(f"⏳ <b>{kod} SÜRE DOLDU</b> ({gun}g) {cur:.2f} → kapat")
            _kaydet_kapali_islem(trades_path, pos, cur, "SURE", bugun)
        else:
            aciklar.append(pos)

    return satlar, aciklar


def korelasyon_filtre(adaylar: List[dict], HIST: dict, max_corr: float = 0.75) -> List[dict]:
    """
    Yüksek korelasyonlu adayları eleyerek çeşitlendirme sağlar.
    Seçilenler arasında |corr| > max_corr olanları atar.
    """
    if len(adaylar) <= 1 or max_corr >= 0.99:
        return adaylar

    from .data import korelasyon_matrisi
    tickers = [a["hisse"] for a in adaylar]
    corr = korelasyon_matrisi(HIST, tickers, lookback=60)
    if corr.empty:
        return adaylar

    secilen = []
    secilen_kod = []
    for a in adaylar:
        kod = a["hisse"]
        if kod not in corr.columns:
            secilen.append(a)
            secilen_kod.append(kod)
            continue
        # Mevcut seçilenlerle korelasyon kontrolü
        yuksek = False
        for s in secilen_kod:
            if s in corr.columns and kod in corr.index:
                c = abs(corr.loc[kod, s])
                if c > max_corr:
                    yuksek = True
                    logger.debug(f"Korelasyon eleme: {kod} ↔ {s} = {c:.2f}")
                    break
        if not yuksek:
            secilen.append(a)
            secilen_kod.append(kod)
    return secilen


def aday_filtrele(sonuclar: List[dict], gecmis: List[dict], firtina: bool,
                  cfg: dict, HIST: dict = None) -> List[dict]:
    """Skor + risk kuralları + korelasyon filtresi ile en iyi adayları seçer."""
    risk = cfg.get("risk", {})
    max_pos = risk.get("firtina_max_pozisyon", 2) if firtina else risk.get("max_pozisyon", 5)
    max_sekt = risk.get("max_sektor", 2)
    rsi_limit = risk.get("rsi_ust_limit", 75)
    min_tahmin = risk.get("min_tahmin", 0.0)
    max_corr = risk.get("max_correlation", 0.75)

    acik_hisseler = {p["hisse"] for p in gecmis}

    aday = sorted(
        [s for s in sonuclar
         if s.get("tahmin", 0) > min_tahmin
         and s.get("rsi", 50) < rsi_limit
         and s["hisse"] not in acik_hisseler],
        key=lambda x: x["tahmin"]
                      + (0.8 if (x.get("rec") or 0) > 0 else 0)
                      + (0.4 if (x.get("relvol") or 0) > 1.8 else 0),
        reverse=True
    )

    # Önce sektör limiti uygula
    top = []
    sekt_say = {}
    for s in aday:
        if len(top) >= max_pos * 2:  # fazla aday al, sonra corr kes
            break
        sek = s.get("sektor", "DIGER")
        if sekt_say.get(sek, 0) >= max_sekt:
            continue
        sekt_say[sek] = sekt_say.get(sek, 0) + 1
        top.append(s)

    # Korelasyon filtresi
    if HIST and len(top) > 1:
        top = korelasyon_filtre(top, HIST, max_corr)

    return top[:max_pos]


def performans_ozeti(trades_path: str) -> str:
    """Kapalı işlemlerden kısa performans özeti üretir."""
    try:
        p = Path(trades_path)
        if not p.exists():
            return ""
        trades = json.load(open(p, encoding="utf-8"))
        if not trades:
            return ""
        # Son 30 işlem
        recent = trades[-30:]
        n = len(recent)
        wins = [t for t in recent if t["getiri_pct"] > 0]
        losses = [t for t in recent if t["getiri_pct"] <= 0]
        wr = len(wins) / n * 100 if n else 0
        avg = np.mean([t["getiri_pct"] for t in recent])
        total = sum(t["getiri_pct"] for t in recent)
        return (
            f"\n📊 <b>Son {n} işlem:</b> Win%{wr:.0f} | "
            f"Ort %{avg:.1f} | Küm %{total:.1f}"
        )
    except Exception:
        return ""
