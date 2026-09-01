#!/usr/bin/env python3
"""
BIST Bot Dashboard (Streamlit)
Çalıştırma: streamlit run dashboard.py
"""
import json
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent

st.set_page_config(page_title="BIST Bot Dashboard", page_icon="📈", layout="wide")
st.title("📈 BIST Bot Dashboard v23")

# --- Dosya yükleme ---
def load_json(name, default=None):
    p = ROOT / name
    if p.exists():
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return default if default is not None else []

hafiza = load_json("gecmis_v15.json", [])
plan = load_json("plan.json", {})
trades = load_json("trades_history.json", [])

# --- Sidebar ---
st.sidebar.header("Kontrol")
st.sidebar.markdown(f"**Tarih:** {datetime.now().strftime('%d.%m.%Y %H:%M')}")
if plan.get("tarih"):
    st.sidebar.info(f"Son plan: {plan['tarih']}")
if plan.get("firtina"):
    st.sidebar.error("🔴 FIRTINA MODU")

# --- Metrikler ---
col1, col2, col3, col4 = st.columns(4)
acik_say = len(hafiza)
col1.metric("Açık Pozisyon", acik_say)

if trades:
    recent = trades[-50:]
    wins = sum(1 for t in recent if t.get("getiri_pct", 0) > 0)
    wr = wins / len(recent) * 100
    avg = np.mean([t["getiri_pct"] for t in recent])
    total = sum(t["getiri_pct"] for t in recent)
    col2.metric("Winrate (son 50)", f"%{wr:.0f}")
    col3.metric("Ort. Getiri", f"%{avg:.1f}")
    col4.metric("Kümülatif", f"%{total:.1f}")
else:
    col2.metric("Winrate", "—")
    col3.metric("Ort. Getiri", "—")
    col4.metric("Kümülatif", "—")

st.divider()

# --- Açık Pozisyonlar ---
st.subheader("💼 Açık Pozisyonlar")
if hafiza:
    rows = []
    for p in hafiza:
        son = p.get("son") or p["alis"]
        ret = (son - p["alis"]) / p["alis"] * 100
        rows.append({
            "Hisse": p["hisse"],
            "Alış": p["alis"],
            "Şimdi": son,
            "Getiri %": round(ret, 2),
            "Stop": p.get("stop"),
            "Hedef": p.get("hedef"),
            "Tarih": p.get("tarih"),
            "Partial": "✂️" if p.get("partial") else ""
        })
    df_pos = pd.DataFrame(rows)
    st.dataframe(df_pos, use_container_width=True)
else:
    st.info("Açık pozisyon yok.")

# --- Bugünün Al Listesi ---
st.subheader("🏆 Bugünün / Yarının Al Listesi")
top = plan.get("top", [])
if top:
    rows = []
    for s in top:
        rows.append({
            "Hisse": s.get("hisse"),
            "Sektör": s.get("sektor"),
            "Fiyat": s.get("fiyat"),
            "Tahmin %": round(s.get("tahmin", 0), 2),
            "RSI": round(s.get("rsi", 0), 1),
            "Stop": s.get("stop"),
            "Hedef": s.get("hedef"),
            "Haber": s.get("haber", 0),
            "Başlık": (s.get("baslik") or "")[:50]
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
else:
    st.info("Al listesi boş.")

# --- Kapalı İşlemler ---
st.subheader("📜 Kapalı İşlem Geçmişi")
if trades:
    df_t = pd.DataFrame(trades)
    df_t = df_t.sort_values("satis_tarih", ascending=False)
    # Renk için
    def color_ret(val):
        color = "green" if val > 0 else "red"
        return f"color: {color}"
    st.dataframe(
        df_t[["hisse", "alis", "satis", "getiri_pct", "gun", "neden", "alis_tarih", "satis_tarih"]],
        use_container_width=True,
        height=400
    )

    # Basit grafik
    st.subheader("Getiri Dağılımı")
    st.bar_chart(df_t.set_index("hisse")["getiri_pct"].tail(30))
else:
    st.info("Henüz kapalı işlem kaydı yok. Bot çalıştıkça burada birikecek.")

# --- Performans özeti ---
if trades:
    st.subheader("📊 Performans Özeti")
    c1, c2, c3 = st.columns(3)
    nedenler = pd.Series([t.get("neden") for t in trades]).value_counts()
    c1.write("**Kapanış Nedenleri**")
    c1.bar_chart(nedenler)
    c2.write("**Getiri İstatistikleri**")
    rets = [t["getiri_pct"] for t in trades]
    c2.metric("Toplam İşlem", len(trades))
    c2.metric("En İyi", f"%{max(rets):.1f}")
    c2.metric("En Kötü", f"%{min(rets):.1f}")
    c3.write("**Ortalama Tutma Süresi**")
    avg_gun = np.mean([t.get("gun", 0) for t in trades])
    c3.metric("Gün", f"{avg_gun:.1f}")

st.divider()
st.caption("BIST Bot v23 • Yatırım tavsiyesi değildir • Streamlit Dashboard")
