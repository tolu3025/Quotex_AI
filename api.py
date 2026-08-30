import asyncio
import time
import logging
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from database import init_db, SessionLocal, Signal, Trade
from config import cfg

from feature_engine import format_prompt_data
from openai_predictor import get_prediction
from signal_manager import SignalManager
from quotex_client import QuotexClient
from telegram_bot import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
for noisy in ["pyquotex", "websockets", "urllib3", "httpx"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

bot_task = None
bot_running = False


class SafeLog:
    _history = []
    @classmethod
    def _fmt(cls, *parts):
        try:
            out = [str(p)[:120] if not isinstance(p, Exception) else f"ERR:{type(p).__name__}:{str(p)[:60]}" for p in parts]
            msg = " | ".join(out).replace("\n", " ").replace("\r", "")[:300]
            return f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}"
        except Exception as e:
            return f"[LOG_FAIL] {str(e)[:80]}"
    @classmethod
    def info(cls, *parts):
        line = cls._fmt(*parts); cls._history.append(line); logger.info(line)
    @classmethod
    def error(cls, *parts):
        line = cls._fmt(*parts); cls._history.append(line); logger.error(line)


async def run_bot():
    global bot_running
    init_db()
    qx = QuotexClient()
    manager = SignalManager()
    notifier = TelegramNotifier()
    assets = [a.strip() for a in cfg.ASSET.split(",")]
    asset_cooldown = {}
    hourly_trades = 0
    hourly_reset = datetime.now(timezone.utc)
    last_trade_time = 0.0

    if not await qx.connect():
        logger.error("Bot failed to connect to Quotex")
        return

    bot_running = True
    logger.info("BOT STARTED on Render | Assets: %s", assets)

    while bot_running:
        cycle_start = time.time()
        try:
            async def scan_one(asset):
                if asset in asset_cooldown:
                    now = datetime.now(timezone.utc)
                    if now < asset_cooldown[asset]:
                        return None
                raw = await qx.get_candles(asset, cfg.TIMEFRAME, 100)
                if len(raw) < 30:
                    return None
                try:
                    market_data = format_prompt_data(raw, asset)
                    if not market_data:
                        return None
                    pred = await get_prediction(market_data)
                    pred["asset"] = asset
                    pred["raw_candles"] = raw
                    return pred
                except Exception:
                    return None

            tasks = [scan_one(a) for a in assets]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            best_signal = None
            best_confidence = 0
            for res in results:
                if isinstance(res, Exception) or not res:
                    continue
                conf = res.get("confidence", 0)
                if conf > best_confidence and conf >= cfg.MIN_CONFIDENCE:
                    best_confidence = conf
                    best_signal = res

            if best_signal and not cfg.PAPER_TRADING:
                asset = best_signal["asset"]
                now = datetime.now(timezone.utc)

                if now.hour != hourly_reset.hour:
                    hourly_trades = 0
                    hourly_reset = now

                if time.time() - last_trade_time >= cfg.TRADE_COOLDOWN_SEC and hourly_trades < 5 and manager.daily_loss < cfg.MAX_DAILY_LOSS:
                    age_ms = (time.time() - best_signal.get("timestamp", 0)) * 1000
                    if age_ms <= cfg.SIGNAL_MAX_AGE_MS:
                        direction = "CALL" if best_signal["prediction"] == "UP" else "PUT"
                        result = await qx.place_trade(asset, cfg.AMOUNT, direction, cfg.TIMEFRAME)
                        logger.info("TRADE %s %s @ %s%% | Result: %s", asset, direction, best_confidence, result)
                        asset_cooldown[asset] = now + timedelta(minutes=10)
                        hourly_trades += 1
                        last_trade_time = time.time()
                        await notifier.send_signal(best_signal["prediction"], best_confidence, "GOOD", best_signal["reasoning"])

        except Exception as e:
            logger.exception("Bot cycle error")

        elapsed = time.time() - cycle_start
        await asyncio.sleep(max(0.5, cfg.SCAN_INTERVAL_SEC - elapsed))

    await qx.disconnect()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_task, bot_running
    bot_task = asyncio.create_task(run_bot())
    yield
    bot_running = False
    if bot_task:
        bot_task.cancel()

app = FastAPI(title="Quotex AI Signal Engine", lifespan=lifespan)

@app.get("/")
def root():
    return {
        "status": "online",
        "bot_running": bot_running,
        "asset": cfg.ASSET,
        "paper_trading": cfg.PAPER_TRADING,
        "min_confidence": cfg.MIN_CONFIDENCE,
        "amount": cfg.AMOUNT,
        "scan_interval_sec": cfg.SCAN_INTERVAL_SEC,
        "signal_max_age_ms": cfg.SIGNAL_MAX_AGE_MS,
    }

@app.get("/signals")
def signals(limit: int = 50):
    db = SessionLocal()
    rows = db.query(Signal).order_by(Signal.timestamp.desc()).limit(limit).all()
    db.close()
    return [{
        "id": r.id, "time": r.timestamp.isoformat() if r.timestamp else None,
        "asset": r.asset, "prediction": r.prediction,
        "confidence": r.confidence, "type": r.signal_type
    } for r in rows]

@app.get("/trades")
def trades(limit: int = 50):
    db = SessionLocal()
    rows = db.query(Trade).order_by(Trade.timestamp.desc()).limit(limit).all()
    db.close()
    return [{
        "id": r.id, "time": r.timestamp.isoformat() if r.timestamp else None,
        "asset": r.asset, "direction": r.direction,
        "amount": r.amount, "result": r.result,
        "profit": r.profit, "paper": r.is_paper
    } for r in rows]

@app.get("/dashboard")
def dashboard():
    return FileResponse("static/dashboard.html")
