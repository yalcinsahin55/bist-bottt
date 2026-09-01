import os
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

_def_config = None

def load_config():
    global _def_config
    if _def_config is not None:
        return _def_config
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Ortam değişkenleri ile override
    cfg["telegram"]["token"] = os.environ.get("TELEGRAM_TOKEN", "")
    cfg["mode"] = os.environ.get("MODE", "aksam")
    _def_config = cfg
    return cfg

def get_sektor_map(cfg):
    mapping = {}
    for sektor, hisseler in cfg.get("sektorler", {}).items():
        for h in hisseler:
            mapping[h] = sektor
    return mapping

def sektor_of(ticker: str, cfg) -> str:
    return get_sektor_map(cfg).get(ticker, "DIGER")
