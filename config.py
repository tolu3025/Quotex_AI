import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL")
    QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD")
    QUOTEX_SSID = os.getenv("QUOTEX_SSID")
    QUOTEX_DEMO = os.getenv("QUOTEX_DEMO", "true").lower() == "true"

    ASSET = os.getenv("ASSET", "EURUSD_otc")
    TIMEFRAME = int(os.getenv("TIMEFRAME", "60"))
    AMOUNT = float(os.getenv("AMOUNT", "10.0"))
    MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "70.0"))
    PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
    MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "100.0"))
    MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "50"))

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///quotex_ai.db")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # === OTC TIMING ===
    SIGNAL_MAX_AGE_MS = float(os.getenv("SIGNAL_MAX_AGE_MS", "300.0"))
    SLIPPAGE_MAX_PCT = float(os.getenv("SLIPPAGE_MAX_PCT", "0.05"))
    # RENDER: higher timeout for slow cloud networks
    NETWORK_TIMEOUT = float(os.getenv("NETWORK_TIMEOUT", "45.0"))
    TRADE_COOLDOWN_SEC = float(os.getenv("TRADE_COOLDOWN_SEC", "5.0"))
    SCAN_INTERVAL_SEC = float(os.getenv("SCAN_INTERVAL_SEC", "3.0"))
    CANDLE_TIMEOUT_SEC = float(os.getenv("CANDLE_TIMEOUT_SEC", "20.0"))

cfg = Config()
