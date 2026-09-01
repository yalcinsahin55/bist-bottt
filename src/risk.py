"""Pozisyon yönetimi, rejim, trailing, partial TP, korelasyon, sektör momentum, paper trading."""
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging
import json
from pathlib import Path

import pandas as pd
import numpy as np

logger = logging.getLogger("bist-bot.risk")


def piyasa_rejimi(HIST: dict, tv_xu100: list = None) -> Tuple[str, bool]:
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


def sektor_momentum(HIST: dict, cfg: dict) -> Dict[str, float]:
    sektorler = cfg.get("sektorler", {})
    skorlar = {}
    for sek, hisseler in sektorler.items():
        rets = []
        for h in hisseler:
            df = HIST.get(h)
            if df is None or len(df) < 25:
                continue
            try:
                r = float(df["Close"].iloc[-1] / df["Close"].iloc[-21] - 1)
                rets.append(r)
            except Exception:
                pass
        if rets:
            skorlar[sek] = float(np.mean(rets) * 100)
    return skorlar


def risk_skoru(firtina: bool, sektor_mom: dict, acik_say: int, max_pos: int) -> Tuple[int, str]:
    skor = 50
    notlar = []
    if firtina:
        skor -= 25
        notlar.append("Fırtına")
    else:
        skor += 10
    if sektor_mom:
        ort = np.mean(list(sektor_mom.values()))
        if ort > 3:
            skor += 15
            notlar.append("Sektörler güçlü")
        elif ort < -3:
            skor -= 15
            notlar.append("Sektörler zayıf")
    doluluk = acik_say / max(max_pos, 1)
    if doluluk > 0.8:
        skor -= 10
        notlar.append("Portföy dolu")
    skor = int(max(0, min(100, skor)))
    if skor >= 70:
        seviye = "🟢 Düşük Risk"
    elif skor >= 45:
        seviye = "🟡 Orta Risk"
    else:
        seviye = "🔴 Yüksek Risk"
    return skor, f"{seviye} ({skor}/100) " + ("| " + ", ".join(notlar) if notlar else "")


def atr_pozisyon_yuzde(atr_pct: float, max_kasa: float = 0.20) -> float:
    if atr_pct <= 0:
        return max_kasa * 0.5
    hedef_risk = 0.015
    stop_mesafe = max(atr_pct / 100 * 2.0, 0.03)
    yuzde = hedef_risk / stop_mesafe
    return float(min(max(yuzde, 0.05), max_kasa))


def _kaydet_kapali_islem(trades_path: str, pos: dict, kapanis_fiyat: float,
                         neden: str, bugun: datetime):
    try:
        trades = []
        p = Path(trades_path)
        if p.exists():
            trades = json.load(open(p, encoding="utf-8"))
        alis = pos["alis"]
        ret = (kapanis_fiyat - alis) / alis * 100
        gun = (bugun - datetime.fromisoformat(pos["tarih"])).days
        kayit = {
            "hisse": pos["hisse"], "alis": alis, "satis": round(kapanis_fiyat, 2),
            "getiri_pct": round(ret, 2), "gun": gun, "neden": neden,
            "alis_tarih": pos["tarih"], "satis_tarih": bugun.strftime("%Y-%m-%d"),
            "stop": pos.get("stop"), "hedef": pos.get("hedef"),
            "partial": pos.get("partial", False), "paper": pos.get("paper", False)
        }
        trades.append(kayit)
        trades = trades[-500:]
        json.dump(trades, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Trade kaydetme: {e}")


def pozisyonlari_guncelle(gecmis, fiyatlar, guncel, bugun, cfg, trades_path):
    risk = cfg.get("risk", {})
    max_gun = risk.get("max_tutma_gun", 10)
    trailing_aktif = risk.get("trailing_aktif", True)
    trail_act = risk.get("trailing_aktivasyon_pct", 0.04)
    trail_mesafe = risk.get("trailing_mesafe_pct", 0.025)
    partial_aktif = risk.get("partial_tp_aktif", True)
    partial_pct = risk.get("partial_tp_pct", 0.06)

    satlar, aciklar = [], []
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

        if partial_aktif and not pos.get("partial") and kar_pct >= partial_pct:
            pos["partial"] = True
            pos["stop"] = max(pos.get("stop", 0), round(pos["alis"] * 1.005, 2))
            satlar.append(f"💰 <b>{kod}</b> PARTIAL TP %{kar_pct*100:.1f} → stop BE")

        if trailing_aktif and kar_pct >= trail_act:
            yeni_stop = round(cur * (1 - trail_mesafe), 2)
            if yeni_stop > pos.get("stop", 0):
                eski = pos["stop"]
                pos["stop"] = yeni_stop
                if yeni_stop > eski * 1.01:
                    satlar.append(f"🔼 <b>{kod}</b> TRAILING {eski} → {yeni_stop}")

        if cur >= pos["hedef"]:
            satlar.append(f"🎯 <b>{kod} HEDEF!</b> {cur:.2f} → KAR AL")
            _kaydet_kapali_islem(trades_path, pos, cur, "HEDEF", bugun)
        elif cur <= pos["stop"]:
            neden = "TRAILING_STOP" if (pos.get("partial") or kar_pct > 0) else "STOP"
            satlar.append(f"🛑 <b>{kod} STOP</b> {cur:.2f}")
            _kaydet_kapali_islem(trades_path, pos, cur, neden, bugun)
        elif gun >= max_gun:
            satlar.append(f"⏳ <b>{kod} SÜRE</b> ({gun}g) {cur:.2f}")
            _kaydet_kapali_islem(trades_path, pos, cur, "SURE", bugun)
        else:
            aciklar.append(pos)
    return satlar, aciklar


def korelasyon_filtre(adaylar, HIST, max_corr=0.75):
    if len(adaylar) <= 1 or max_corr >= 0.99:
        return adaylar
    from .data import korelasyon_matrisi
    tickers = [a["hisse"] for a in adaylar]
    corr = korelasyon_matrisi(HIST, tickers, lookback=60)
    if corr.empty:
        return adaylar
    secilen, secilen_kod = [], []
    for a in adaylar:
        kod = a["hisse"]
        if kod not in corr.columns:
            secilen.append(a)
            secilen_kod.append(kod)
            continue
        yuksek = False
        for s in secilen_kod:
            if s in corr.columns and kod in corr.index:
                if abs(corr.loc[kod, s]) > max_corr:
                    yuksek = True
                    break
        if not yuksek:
            secilen.append(a)
            secilen_kod.append(kod)
    return secilen


def aday_filtrele(sonuclar, gecmis, firtina, cfg, HIST=None):
    risk = cfg.get("risk", {})
    max_pos = risk.get("firtina_max_pozisyon", 2) if firtina else risk.get("max_pozisyon", 5)
    max_sekt = risk.get("max_sektor", 2)
    rsi_limit = risk.get("rsi_ust_limit", 75)
    min_tahmin = risk.get("min_tahmin", 0.0)
    max_corr = risk.get("max_correlation", 0.75)
    acik_hisseler = {p["hisse"] for p in gecmis}
    aday = sorted(
        [s for s in sonuclar if s.get("tahmin", 0) > min_tahmin and s.get("rsi", 50) < rsi_limit and s["hisse"] not in acik_hisseler],
        key=lambda x: x["tahmin"] + (0.8 if (x.get("rec") or 0) > 0 else 0) + (0.4 if (x.get("relvol") or 0) > 1.8 else 0) + (0.3 if (x.get("sektor_mom") or 0) > 2 else 0),
        reverse=True
    )
    top, sekt_say = [], {}
    for s in aday:
        if len(top) >= max_pos * 2:
            break
        sek = s.get("sektor", "DIGER")
        if sekt_say.get(sek, 0) >= max_sekt:
            continue
        sekt_say[sek] = sekt_say.get(sek, 0) + 1
        top.append(s)
    if HIST and len(top) > 1:
        top = korelasyon_filtre(top, HIST, max_corr)
    return top[:max_pos]


def performans_ozeti(trades_path: str) -> str:
    try:
        p = Path(trades_path)
        if not p.exists():
            return ""
        trades = json.load(open(p, encoding="utf-8"))
        if not trades:
            return ""
        recent = trades[-30:]
        n = len(recent)
        wins = [t for t in recent if t["getiri_pct"] > 0]
        wr = len(wins) / n * 100 if n else 0
        avg = np.mean([t["getiri_pct"] for t in recent])
        total = sum(t["getiri_pct"] for t in recent)
        return f"\n📊 Son {n} işlem: Win%{wr:.0f} | Ort %{avg:.1f} | Küm %{total:.1f}"
    except Exception:
        return ""


# ---------- Paper Trading ----------
def paper_yukle(path: str, baslangic: float = 100000) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"kasa": baslangic, "baslangic": baslangic, "pozisyonlar": [], "gecmis": [], "son_guncelleme": None}


def paper_kaydet(path: str, data: dict):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def paper_al(paper: dict, hisse: str, fiyat: float, yuzde: float, stop: float, hedef: float, tarih: str) -> dict:
    """Sanal alım yap. yuzde = kasa yüzdesi (0-100)."""
    kasa = paper.get("kasa", 0)
    tutar = kasa * (yuzde / 100.0)
    if tutar < 100 or fiyat <= 0:
        return paper
    adet = tutar / fiyat
    maliyet = adet * fiyat
    if maliyet > kasa:
        return paper
    paper["kasa"] = round(kasa - maliyet, 2)
    paper["pozisyonlar"].append({
        "hisse": hisse,
        "alis": round(fiyat, 2),
        "adet": round(adet, 4),
        "maliyet": round(maliyet, 2),
        "stop": stop,
        "hedef": hedef,
        "tarih": tarih,
        "son": round(fiyat, 2)
    })
    paper["son_guncelleme"] = tarih
    logger.info(f"Paper AL: {hisse} {adet:.2f} adet @ {fiyat} = {maliyet:.0f} TL")
    return paper


def paper_guncelle(paper: dict, guncel: Dict[str, float], bugun: datetime, cfg: dict, trades_path: str) -> Tuple[dict, List[str]]:
    """Açık sanal pozisyonları güncelle, stop/hedef kontrol et."""
    risk = cfg.get("risk", {})
    max_gun = risk.get("max_tutma_gun", 10)
    mesajlar = []
    kalan = []
    for pos in paper.get("pozisyonlar", []):
        kod = pos["hisse"]
        cur = guncel.get(kod) or pos.get("son")
        if cur is None:
            kalan.append(pos)
            continue
        pos["son"] = cur
        gun = (bugun - datetime.fromisoformat(pos["tarih"])).days
        kar_pct = (cur - pos["alis"]) / pos["alis"] * 100
        deger = pos["adet"] * cur

        kapat = False
        neden = ""
        if cur >= pos["hedef"]:
            kapat, neden = True, "HEDEF"
            mesajlar.append(f"🎯 SANAL {kod} HEDEF %{kar_pct:.1f}")
        elif cur <= pos["stop"]:
            kapat, neden = True, "STOP"
            mesajlar.append(f"🛑 SANAL {kod} STOP %{kar_pct:.1f}")
        elif gun >= max_gun:
            kapat, neden = True, "SURE"
            mesajlar.append(f"⏳ SANAL {kod} SÜRE %{kar_pct:.1f}")

        if kapat:
            paper["kasa"] = round(paper["kasa"] + deger, 2)
            paper.setdefault("gecmis", []).append({
                "hisse": kod, "alis": pos["alis"], "satis": round(cur, 2),
                "getiri_pct": round(kar_pct, 2), "gun": gun, "neden": neden,
                "tarih": bugun.strftime("%Y-%m-%d"), "maliyet": pos["maliyet"],
                "kar_tl": round(deger - pos["maliyet"], 2)
            })
            _kaydet_kapali_islem(trades_path, {**pos, "paper": True}, cur, f"PAPER_{neden}", bugun)
        else:
            kalan.append(pos)

    paper["pozisyonlar"] = kalan
    paper["son_guncelleme"] = bugun.strftime("%Y-%m-%d")
    return paper, mesajlar


def paper_ozet(paper: dict) -> str:
    kasa = paper.get("kasa", 0)
    bas = paper.get("baslangic", 100000)
    poz = paper.get("pozisyonlar", [])
    acik_deger = sum(p.get("adet", 0) * p.get("son", p.get("alis", 0)) for p in poz)
    toplam = kasa + acik_deger
    getiri = (toplam / bas - 1) * 100 if bas else 0
    satir = f"\n💼 <b>SANAL PORTFÖY</b>\nNakit: {kasa:,.0f} TL | Açık: {len(poz)} | Toplam ≈ {toplam:,.0f} TL ({'+' if getiri>=0 else ''}{getiri:.1f}%)"
    if poz:
        satir += "\n"
        for p in poz:
            son = p.get("son", p["alis"])
            f2 = (son - p["alis"]) / p["alis"] * 100
            satir += f"• {p['hisse']} {p['alis']:.2f}→{son:.2f} {'+' if f2>=0 else ''}{f2:.1f}%\n"
    return satir
