# BIST Bot v23

Borsa İstanbul (BIST) için otomatik hisse tarama, ML tabanlı tahmin, gelişmiş risk yönetimi ve Telegram raporlama botu.

## Yeni Özellikler (v23)

- **Gerçek OHLCV** güncelleme (yfinance ile)
- **Trailing Stop** (kâr %4'ü geçince aktif)
- **Partial Take-Profit** (%6 kârda yarısını sat + stop BE)
- **Korelasyon filtresi** (yüksek korelasyonlu hisseleri ele)
- **Detaylı işlem geçmişi** (`trades_history.json`) + performans özeti
- **Streamlit Dashboard** (`dashboard.py`)
- LightGBM + 24 feature
- Sektör + fırtına modu + mcap filtreleri

## Kurulum

1. Repo'yu yükle / fork'la.
2. GitHub Secrets'a `TELEGRAM_TOKEN` ekle.
3. `config.yaml` → `telegram.chat_id` değerini kendi chat id'n ile değiştir.
4. Actions'ı etkinleştir.

### Yerel çalıştırma

```bash
pip install -r requirements.txt

# Bot
export TELEGRAM_TOKEN="xxx"
export MODE=aksam          # sabah / backtest
python bot.py

# Dashboard
streamlit run dashboard.py
```

## Dosya Yapısı

```
bist-bot-improved/
├── bot.py
├── dashboard.py              ← Streamlit arayüz
├── config.yaml               ← Tüm parametreler
├── requirements.txt
├── bist_history.csv
├── gecmis_v15.json           ← Açık pozisyonlar
├── plan.json
├── trades_history.json       ← Kapalı işlemler
├── src/
│   ├── data.py               (TV + yfinance + korelasyon)
│   ├── features.py
│   ├── model.py
│   ├── risk.py               (trailing, partial, corr)
│   ├── bot_core.py
│   ├── backtest.py
│   └── ...
└── .github/workflows/bot.yml
```

## Önemli Config Parametreleri

```yaml
risk:
  trailing_aktif: true
  trailing_aktivasyon_pct: 0.04
  trailing_mesafe_pct: 0.025
  partial_tp_aktif: true
  partial_tp_pct: 0.06
  max_correlation: 0.75
data:
  yfinance_guncelle: true
  yfinance_max_hisse: 40
```

## Uyarı

Bu yazılım **yatırım tavsiyesi değildir**. Eğitim ve araştırma amaçlıdır. Gerçek para ile kullanmadan önce kendi risk yönetiminizi uygulayın.
