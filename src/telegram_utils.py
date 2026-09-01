"""Telegram mesaj gönderme."""
import logging
import requests

logger = logging.getLogger("bist-bot.tg")


def telegram_gonder(token: str, chat_id: str, mesaj: str) -> bool:
    if not token:
        logger.warning("TELEGRAM_TOKEN yok, mesaj gönderilmedi")
        print(mesaj)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": mesaj, "parse_mode": "HTML"},
            timeout=15
        )
        if r.status_code == 200:
            return True
        logger.error(f"TG status {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"TG hata: {e}")
        return False
