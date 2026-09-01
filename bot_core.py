"""Ana akşam / sabah iş mantığı."""
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .config_loader import load_config, sektor_of
from .data import (
    trt_now, tv_tarama, alanlar, csv_yukle, csv_guncelle_tv,
    haber_bayrak, yfinance_ohlcv_guncelle, KOLONLAR_EXT, KOLONLAR_BASIC
)
from .features import skor_teknik
from .model import analiz_ml
from .risk import (
    piyasa_rejimi, makro_ozet, pozisyonlari_guncelle,
    aday_filtrele, performans_ozeti
)
from .telegram_utils import telegram_gonder
from .backtest import run_backtest

logger = logging.getLogger("bist-bot.core")


def aksam(cfg: dict):
    root = Path(__file__).resolve().parent.parent
    hafiza_path = root / cfg["files"]["hafiza"]
    plan_path = root / cfg["files"]["plan"]
    csv_path = root / cfg["files"]["csv"]
    trades_path = str(root / cfg["files"].get("trades", "trades_history.json"))

    # Hafıza yükle
    gecmis = []
    if hafiza_path.exists():
        try:
            gecmis = json.load(open(hafiza_path, encoding="utf-8"))
        except Exception:
            gecmis = []

    HIST = csv_yukle(str(csv_path))
    hisseler = cfg.get("hisseler", [])

    # yfinance ile gerçek OHLCV zenginleştir (opsiyonel, yavaş olabilir)
    try:
        yfinance_ohlcv_guncelle(hisseler, str(csv_path), cfg)
        # Yeniden yükle
        HIST = csv_yukle(str(csv_path))
    except Exception as e:
        logger.warning(f"yfinance güncelleme atlandı: {e}")

    # Canlı veri
    ham = tv_tarama([f"BIST:{k}" for k in hisseler], KOLONLAR_EXT)
    if len(ham) < 40:
        ham = tv_tarama([f"BIST:{k}" for k in hisseler], KOLONLAR_BASIC)
    if len(ham) < 30:
        telegram_gonder(
            cfg["telegram"]["token"],
            cfg["telegram"]["chat_id"],
            "⚠️ <b>VERİ UYARISI:</b> TradingView'a ulaşılamadı."
        )
        return

    GUNCEL = {k: v[0] for k, v in ham.items()}
    ML_AKTIF = len(HIST) >= 40
    logger.info(f"TV: {len(ham)} | CSV: {len(HIST)} | ML: {ML_AKTIF}")

    # Analiz
    sonuclar = []
    if ML_AKTIF:
        for k in hisseler:
            r = analiz_ml(k, GUNCEL, HIST, cfg)
            if r:
                sonuclar.append(r)

    if not sonuclar:
        ML_AKTIF = False
        for k in hisseler:
            if k not in ham:
                continue
            r = skor_teknik(alanlar(ham[k]))
            if r and r["skor"] >= 3:
                risk = cfg["risk"]
                stop_pct = min(max(2 * r["atrp"] / 100, risk["stop_min_pct"]), risk["stop_max_pct"])
                r.update({
                    "hisse": k,
                    "sektor": sektor_of(k, cfg),
                    "fiyat": r["close"],
                    "tahmin": r["skor"] * 1.2,
                    "stop": round(r["close"] * (1 - stop_pct), 2),
                    "hedef": round(r["close"] * (1 + risk["hedef_risk_odul"] * stop_pct), 2),
                    "motor": "SKOR"
                })
                sonuclar.append(r)

    # TV ek bilgileri + filtre
    for s in sonuclar:
        a = alanlar(ham.get(s["hisse"], []))
        s["relvol"] = a.get("relvol")
        s["rec"] = a.get("rec")
        s["mcap"] = a.get("mcap")

    risk = cfg["risk"]
    sonuclar = [
        s for s in sonuclar
        if (s.get("mcap") is None or s["mcap"] >= risk.get("min_mcap", 2e9))
        and (s.get("rec") is None or s["rec"] > risk.get("min_rec", -0.5))
    ]

    if not sonuclar:
        telegram_gonder(
            cfg["telegram"]["token"], cfg["telegram"]["chat_id"],
            "⚠️ Veri var ama aday yok."
        )
        return

    bugun = trt_now()
    pmetin, firtina = piyasa_rejimi(HIST)
    fiyatlar = {s["hisse"]: s["fiyat"] for s in sonuclar}

    # Pozisyon güncelle (trailing + partial + kayıt)
    satlar, aciklar = pozisyonlari_guncelle(
        gecmis, fiyatlar, GUNCEL, bugun, cfg, trades_path
    )
    gecmis = aciklar

    # Yeni adaylar (korelasyon dahil)
    top = aday_filtrele(sonuclar, gecmis, firtina, cfg, HIST)

    # Haber bayrağı
    for s in top:
        n, b = haber_bayrak(s["hisse"])
        s["haber"] = n
        s["baslik"] = b
        time.sleep(0.25)

    # Hafızaya ekle
    for s in top:
        gecmis.append({
            "hisse": s["hisse"],
            "alis": round(s["fiyat"], 2),
            "hedef": s["hedef"],
            "stop": s["stop"],
            "tarih": bugun.strftime("%Y-%m-%d"),
            "son": s["fiyat"],
            "partial": False
        })

    json.dump(gecmis, open(hafiza_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(
        {"tarih": bugun.strftime("%Y-%m-%d"), "top": top, "firtina": firtina},
        open(plan_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2
    )

    # CSV yedek güncelleme (TV close)
    csv_guncelle_tv(str(csv_path), ham, bugun)

    # Mesaj
    motor = "🧠 LightGBM / ML" if ML_AKTIF else "🧠 Teknik Skor"
    perf = performans_ozeti(trades_path)
    mesaj = (
        f"🤖 <b>AKŞAM RAPORU v23</b>\n"
        f"📅 {bugun.strftime('%d.%m.%Y')}\n"
        f"Motor: {motor}\n"
        f"🔧 TV:{len(ham)} | CSV:{len(HIST)} | ADAY:{len(sonuclar)}\n"
        f"🌡️ {pmetin}\n{makro_ozet(HIST)}"
        f"{perf}\n"
        f"━━━━━━━━━━━━━━━\n<b>📉 SAT / KAPAT / TRAIL:</b>\n"
    )
    mesaj += "\n".join(satlar) + "\n" if satlar else "✅ Uyarı yok\n"
    mesaj += "━━━━━━━━━━━━━━━\n<b>💼 AÇIK POZİSYONLAR:</b>\n"
    if gecmis:
        for p in gecmis:
            son = p.get("son")
            partial_flag = " ✂️" if p.get("partial") else ""
            if son is None:
                mesaj += f"• {p['hisse']}{partial_flag} | {p['alis']:.2f} | —\n"
            else:
                f2 = (son - p["alis"]) / p["alis"] * 100
                mesaj += (
                    f"• {p['hisse']}{partial_flag} | Alış {p['alis']:.2f} | Şimdi {son:.2f} | "
                    f"{'+' if f2 >= 0 else ''}{f2:.1f}% | 🛑{p['stop']} 🎯{p['hedef']}\n"
                )
    else:
        mesaj += "— Yok\n"

    mesaj += "━━━━━━━━━━━━━━━\n<b>🏆 YARIN İÇİN AL LİSTESİ:</b>\n"
    if top:
        for i, s in enumerate(top, 1):
            yildiz = " ⭐" if (s.get("rec") or 0) > 0 else ""
            hacim = " 🔊" if (s.get("relvol") or 0) > 1.8 else ""
            haber = " 📰" if s.get("haber") else ""
            mesaj += (
                f"\n<b>{i}. {s['hisse']}</b> ({s['sektor']}){yildiz}{hacim}{haber} "
                f"💰{s['fiyat']:.2f}\n"
                f"📈%{s['tahmin']:.2f} | RSI:{s['rsi']:.1f}\n"
                f"🛑{s['stop']} 🎯{s['hedef']}\n"
            )
            if s.get("baslik"):
                mesaj += f"📰 <i>{s['baslik']}</i>\n"
        mesaj += (
            "\n⭐ TV AL | 🔊 hacim | 📰 haber | ✂️ partial TP aktif\n"
            f"💡 Kasa: hisse başı max %{int(risk.get('max_kasa_yuzde', 0.2)*100)}. "
            f"Korelasyon filtresi: {risk.get('max_correlation', 0.75)}\n"
        )
    else:
        mesaj += "\n😴 Bugün liste boş. Nakit de pozisyondur.\n"
    mesaj += "⚠️ <i>Yatırım tavsiyesi değildir.</i>"

    print(mesaj)
    telegram_gonder(cfg["telegram"]["token"], cfg["telegram"]["chat_id"], mesaj)


def sabah(cfg: dict):
    root = Path(__file__).resolve().parent.parent
    plan_path = root / cfg["files"]["plan"]
    hafiza_path = root / cfg["files"]["hafiza"]
    csv_path = root / cfg["files"]["csv"]
    trades_path = str(root / cfg["files"].get("trades", "trades_history.json"))

    plan, gecmis = {}, []
    try:
        plan = json.load(open(plan_path, encoding="utf-8"))
    except Exception:
        pass
    try:
        gecmis = json.load(open(hafiza_path, encoding="utf-8"))
    except Exception:
        pass

    HIST = csv_yukle(str(csv_path))
    GUNCEL = {}
    if gecmis:
        ham = tv_tarama([f"BIST:{p['hisse']}" for p in gecmis], ["close"])
        GUNCEL = {k: v[0] for k, v in ham.items()}

    mesaj = f"🌅 <b>SABAH BRİEFİNGİ</b> 📅 {trt_now().strftime('%d.%m.%Y %H:%M')}\n"
    if plan.get("firtina"):
        mesaj += "🔴 FIRTINA MODU aktif.\n"
    mesaj += makro_ozet(HIST)
    mesaj += performans_ozeti(trades_path) + "\n"
    mesaj += "━━━━━━━━━━━━━━━\n<b>🛒 BUGÜNÜN AL LİSTESİ:</b>\n"
    if plan.get("top"):
        for s in plan["top"]:
            haber = " 📰" if s.get("haber") else ""
            mesaj += (
                f"• <b>{s['hisse']}</b>{haber} 💰{s['fiyat']:.2f} | "
                f"kovalama: {s['fiyat']*1.01:.2f} | 🛑{s['stop']} 🎯{s['hedef']}\n"
            )
    else:
        mesaj += "• Bugün alım yok.\n"
    mesaj += "━━━━━━━━━━━━━━━\n<b>💼 AÇIKLAR:</b>\n"
    if gecmis:
        for p in gecmis:
            cur = GUNCEL.get(p["hisse"]) or p.get("son") or p["alis"]
            f2 = (cur - p["alis"]) / p["alis"] * 100
            partial = " ✂️" if p.get("partial") else ""
            mesaj += f"• {p['hisse']}{partial} {cur:.2f} ({'+' if f2 >= 0 else ''}{f2:.1f}%)\n"
    else:
        mesaj += "— Yok\n"
    mesaj += "\n💡 Kovalama sınırını aşarsa VAZGEÇ."
    print(mesaj)
    telegram_gonder(cfg["telegram"]["token"], cfg["telegram"]["chat_id"], mesaj)


def backtest_mode(cfg: dict):
    root = Path(__file__).resolve().parent.parent
    csv_path = root / cfg["files"]["csv"]
    HIST = csv_yukle(str(csv_path))
    mesaj = run_backtest(HIST, cfg)
    print(mesaj)
    telegram_gonder(cfg["telegram"]["token"], cfg["telegram"]["chat_id"], mesaj)
