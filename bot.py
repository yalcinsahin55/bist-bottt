#!/usr/bin/env python3
"""
BIST Bot v22 - Geliştirilmiş versiyon
Çalıştırma:
  MODE=aksam python bot.py
  MODE=sabah python bot.py
  MODE=backtest python bot.py
"""
import logging
import sys
from pathlib import Path

# src klasörünü path'e ekle
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config_loader import load_config
from src.bot_core import aksam, sabah, backtest_mode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bist-bot")


def main():
    cfg = load_config()
    mode = cfg.get("mode", "aksam")
    logger.info(f"🚀 MODE: {mode}")

    if mode == "sabah":
        sabah(cfg)
    elif mode == "backtest":
        backtest_mode(cfg)
    else:
        aksam(cfg)


if __name__ == "__main__":
    main()
