"""Ana akşam / sabah iş mantığı - v24"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .config_loader import load_config, sektor_of
from .data import (
    trt_now, tv_tarama, alanlar, csv_yukle, csv_guncelle_tv,
    haber_bayrak, yfinance_ohlcv_guncelle, KOLONLAR_EXT, KOLONLAR_BASIC
)
from .features import skor_teknik
from .model import analiz_ml
from .risk import (
    piyasa_rejimi, makro_ozet, pozisyonlari_guncelle,
    aday_filtrele, performans_ozeti, sektor_momentum, risk_skoru,
    atr_pozisyon_yuzde, paper_yukle, paper_kaydet, paper_ozet
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
    paper_path = str(root / cfg["files"].get("paper", "paper_portfolio.json"))

    gecmis = []
    if hafiza_path.exists():
        try:
            gecmis = json.load(open(hafiza_path, encoding="utf-8"))
        except Exception:
            gecmis = []

    HIST = csv_yukle(str(csv_path))
    hisseler = cfg.get("hisseler", [])

    try:
        yfinance_ohlcv_guncelle(hisseler, str(csv_path), cfg)
        HIST = csv_yukle(str(csv_path))
    except Exception as e:
        logger.warning(f"yfinance: {e}")

    ham = tv_tarama([f"BIST:{k}" for k in hisseler], KOLONLAR_EXT)
    if len(ham) < 40:
        ham = tv_tarama([f"BIST:{k}" for k in hisseler], KOLONLAR_BASIC)
    if len(ham) < 30:
        telegram_gonder(cfg["telegram"]["token"], cfg["telegram"]["chat_id"],
                        "⚠️ <b>VERİ UYARISI:</b> TradingView'a ulaşılamadı.")
        return

    GUNCEL = {k: v[0] for k, v in ham.items()}
    ML_AKTIF = len(HIST) >= 40
    logger.info(f"TV:{len(ham)} CSV:{len(HIST)} ML:{ML_AKTIF}")

    # Sektör momentum
    sek_mom = sektor_momentum(HIST, cfg)

    sonuclar = []
    if ML_AKTIF:
        for k in hisseler:
            r = analiz_ml(k, GUNCEL, HIST, cfg)
            if r:
                r["sektor_mom"] = sek_mom.get(r.get("sektor"), 0)
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
                    "hisse": k, "sektor": sektor_of(k, cfg),
                    "fiyat": r["close"], "tahmin": r["skor"] * 1.2,
                    "stop": round(r["close"] * (1 - stop_pct), 2),
                    "hedef": round(r["close"] * (1 + risk["hedef_risk_odul"] * stop_pct), 2),
                    "motor": "SKOR", "sektor_mom": sek_mom.get(sektor_of(k, cfg), 0)
                })
                sonuclar.append(r)

    for s in sonuclar:
        a = alanlar(ham.get(s["hisse"], []))
        s["relvol"] = a.get("relvol")
        s["rec"] = a.get("rec")
        s["mcap"] = a.get("mcap")
        # ATR % için yaklaşık
        if s.get("atr") and s.get("fiyat"):
            s["atr_pct"] = s["atr"] / s["fiyat"] * 100
        else:
            s["atr_pct"] = a.get("atr") and s.get("fiyat") and (a["atr"] / s["fiyat"] * 100) or 3.0

    risk = cfg["risk"]
    sonuclar = [
        s for s in sonuclar
        if (s.get("mcap") is None or s["mcap"] >= risk.get("min_mcap", 2e9))
        and (s.get("rec") is None or s["rec"] > risk.get("min_rec", -0.5))
    ]

    if not sonuclar:
        telegram_gonder(cfg["telegram"]["token"], cfg["telegram"]["chat_id"], "⚠️ Aday yok.")
        return

    bugun = trt_now()
    pmetin, firtina = piyasa_rejimi(HIST)
    fiyatlar = {s["hisse"]: s["fiyat"] for s in sonuclar}

    satlar, aciklar = pozisyonlari_guncelle(gecmis, fiyatlar, GUNCEL, bugun, cfg, trades_path)
    gecmis = aciklar

    top = aday_filtrele(sonuclar, gecmis, firtina, cfg, HIST)

    for s in top:
        n, b = haber_bayrak(s["hisse"])
        s["haber"] = n
        s["baslik"] = b
        # ATR bazlı pozisyon önerisi
        if risk.get("atr_pozisyon_aktif", True):
            s["poz_yuzde"] = round(atr_pozisyon_yuzde(s.get("atr_pct", 3.0), risk.get("max_kasa_yuzde", 0.20)) * 100, 1)
        else:
            s["poz_yuzde"] = int(risk.get("max_kasa_yuzde", 0.20) * 100)
        time.sleep(0.2)

    # Hafızaya ekle (sinyal olarak)
    for s in top:
        gecmis.append({
            "hisse": s["hisse"], "alis": round(s["fiyat"], 2),
            "hedef": s["hedef"], "stop": s["stop"],
            "tarih": bugun.strftime("%Y-%m-%d"), "son": s["fiyat"],
            "partial": False, "paper": False
        })

    json.dump(gecmis, open(hafiza_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"tarih": bugun.strftime("%Y-%m-%d"), "top": top, "firtina": firtina,
               "sektor_mom": sek_mom}, open(plan_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    csv_guncelle_tv(str(csv_path), ham, bugun)

    # Paper portfolio özeti
    paper = paper_yukle(paper_path, risk.get("paper_baslangic_kasa", 100000))
    paper_msg = paper_ozet(paper)

    # Risk skoru
    rskor, rmetin = risk_skoru(firtina, sek_mom, len(gecmis), risk.get("max_pozisyon", 5))

    # Sektör momentum özeti (en iyi 3)
    sek_sirali = sorted(sek_mom.items(), key=lambda x: -x[1])[:3]
    sek_txt = " | ".join([f"{k} %{v:+.1f}" for k, v in sek_sirali]) if sek_sirali else "—"

    motor = "🧠 LightGBM" if ML_AKTIF else "🧠 Teknik Skor"
    perf = performans_ozeti(trades_path)

    mesaj = (
        f"🤖 <b>AKŞAM RAPORU v24</b>\n"
        f"📅 {bugun.strftime('%d.%m.%Y')}\n"
        f"Motor: {motor} | TV:{len(ham)} CSV:{len(HIST)}\n"
        f"🌡️ {pmetin}\n{makro_ozet(HIST)}\n"
        f"⚖️ {rmetin}\n"
        f"🏭 Sektör: {sek_txt}"
        f"{perf}{paper_msg}\n"
        f"━━━━━━━━━━━━━━━\n<b>📉 SAT / KAPAT / TRAIL:</b>\n"
    )
    mesaj += ("\n".join(satlar) + "\n") if satlar else "✅ Uyarı yok\n"
    mesaj += "━━━━━━━━━━━━━━━\n<b>💼 AÇIK POZİSYONLAR:</b>\n"
    if gecmis:
        for p in gecmis:
            son = p.get("son")
            partial = " ✂️" if p.get("partial") else ""
            if son is None:
                mesaj += f"• {p['hisse']}{partial} | {p['alis']:.2f}\n"
            else:
                f2 = (son - p["alis"]) / p["alis"] * 100
                mesaj += (f"• {p['hisse']}{partial} | {p['alis']:.2f}→{son:.2f} "
                          f"{'+' if f2>=0 else ''}{f2:.1f}% | 🛑{p['stop']} 🎯{p['hedef']}\n")
    else:
        mesaj += "— Yok (temiz başlangıç)\n"

    mesaj += "━━━━━━━━━━━━━━━\n<b>🏆 YARIN İÇİN AL LİSTESİ:</b>\n"
    if top:
        for i, s in enumerate(top, 1):
            yildiz = " ⭐" if (s.get("rec") or 0) > 0 else ""
            hacim = " 🔊" if (s.get("relvol") or 0) > 1.8 else ""
            haber = " 📰" if s.get("haber") else ""
            mom = f" 🏭%{s.get('sektor_mom',0):+.0f}" if s.get("sektor_mom") else ""
            mesaj += (
                f"\n<b>{i}. {s['hisse']}</b> ({s['sektor']}){yildiz}{hacim}{haber}{mom}\n"
                f"💰{s['fiyat']:.2f} | 📈%{s['tahmin']:.1f} | RSI:{s['rsi']:.0f}\n"
                f"🛑{s['stop']} 🎯{s['hedef']} | Kasa önerisi: %{s.get('poz_yuzde',20)}\n"
            )
            if s.get("baslik"):
                mesaj += f"📰 <i>{s['baslik']}</i>\n"
        mesaj += ("\n⭐TV AL | 🔊hacim | 📰haber | 🏭sektör momentum\n"
                  f"💡 Max kasa %{int(risk.get('max_kasa_yuzde',0.2)*100)} | Korelasyon filtresi aktif\n")
    else:
        mesaj += "\n😴 Liste boş. Nakit de pozisyondur.\n"
    mesaj += "⚠️ <i>Yatırım tavsiyesi değildir. Sanal portföy ayrı çalışır.</i>"

    print(mesaj)
    telegram_gonder(cfg["telegram"]["token"], cfg["telegram"]["chat_id"], mesaj)


def sabah(cfg: dict):
    root = Path(__file__).resolve().parent.parent
    plan_path = root / cfg["files"]["plan"]
    hafiza_path = root / cfg["files"]["hafiza"]
    csv_path = root / cfg["files"]["csv"]
    trades_path = str(root / cfg["files"].get("trades", "trades_history.json"))
    paper_path = str(root / cfg["files"].get("paper", "paper_portfolio.json"))

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

    paper = paper_yukle(paper_path, cfg.get("risk", {}).get("paper_baslangic_kasa", 100000))

    mesaj = f"🌅 <b>SABAH BRİEFİNGİ v24</b> 📅 {trt_now().strftime('%d.%m.%Y %H:%M')}\n"
    if plan.get("firtina"):
        mesaj += "🔴 FIRTINA MODU aktif.\n"
    mesaj += makro_ozet(HIST)
    mesaj += performans_ozeti(trades_path)
    mesaj += paper_ozet(paper) + "\n"
    mesaj += "━━━━━━━━━━━━━━━\n<b>🛒 BUGÜNÜN AL LİSTESİ:</b>\n"
    if plan.get("top"):
        for s in plan["top"]:
            haber = " 📰" if s.get("haber") else ""
            poz = s.get("poz_yuzde", 20)
            mesaj += (f"• <b>{s['hisse']}</b>{haber} 💰{s['fiyat']:.2f} | "
                      f"kovalama ≤{s['fiyat']*1.01:.2f} | 🛑{s['stop']} 🎯{s['hedef']} | %{poz} kasa\n")
    else:
        mesaj += "• Bugün alım yok.\n"
    mesaj += "━━━━━━━━━━━━━━━\n<b>💼 AÇIKLAR:</b>\n"
    if gecmis:
        for p in gecmis:
            cur = GUNCEL.get(p["hisse"]) or p.get("son") or p["alis"]
            f2 = (cur - p["alis"]) / p["alis"] * 100
            partial = " ✂️" if p.get("partial") else ""
            mesaj += f"• {p['hisse']}{partial} {cur:.2f} ({'+' if f2>=0 else ''}{f2:.1f}%)\n"
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
