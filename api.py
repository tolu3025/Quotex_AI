import asyncio
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from database import init_db, SessionLocal, Signal, Trade
from config import cfg

# Import bot components
from feature_engine import format_prompt_data
from openai_predictor import get_prediction
from signal_manager import SignalManager
from quotex_client import QuotexClient
from telegram_bot import TelegramNotifier

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
for noisy in ["pyquotex", "websockets", "urllib3", "httpx"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Bot globals
bot_task = None
bot_running = False

async def run_bot():
    """Background bot loop."""
    global bot_running
    from datetime import datetime, timezone, timedelta
    
    init_db()
    qx = QuotexClient()
    manager = SignalManager()
    notifier = TelegramNotifier()
    assets = [a.strip() for a in cfg.ASSET.split(",")]
    asset_cooldown = {}
    hourly_trades = 0
    hourly_reset = datetime.now(timezone.utc)
    
    if not await qx.connect():
        logger.error("Bot failed to connect to Quotex")
        return
    
    bot_running = True
    logger.info("BOT STARTED on Render | Assets: %s", assets)
    
    while bot_running:
        try:
            best_signal = None
            best_asset = None
            best_confidence = 0
            
            for asset in assets:
                if asset in asset_cooldown:
                    now = datetime.now(timezone.utc)
                    if now < asset_cooldown[asset]:
                        continue
                
                raw = await qx.get_candles(asset, cfg.TIMEFRAME, 100)
                if len(raw) < 30:
                    continue
                
                try:
                    market_data = format_prompt_data(raw, asset)
                    pred = await get_prediction(market_data)
                    conf = pred["confidence"]
                except Exception as e:
                    continue
                
                if conf > best_confidence and conf >= cfg.MIN_CONFIDENCE:
                    best_confidence = conf
                    best_signal = pred
                    best_asset = asset
            
            if best_signal and cfg.PAPER_TRADING is False:
                now = datetime.now(timezone.utc)
                if now.hour != hourly_reset.hour:
                    hourly_trades = 0
                    hourly_reset = now
                
                if hourly_trades < 5:
                    direction = "CALL" if best_signal["prediction"] == "UP" else "PUT"
                    result = await qx.place_trade(best_asset, cfg.AMOUNT, direction, cfg.TIMEFRAME)
                    logger.info("TRADE %s %s @ %s%% | Result: %s", best_asset, direction, best_confidence, result)
                    asset_cooldown[best_asset] = now + timedelta(minutes=10)
                    hourly_trades += 1
                    await notifier.send_signal(best_signal["prediction"], best_confidence, "GOOD", best_signal["reasoning"])
        
        except Exception as e:
            logger.exception("Bot cycle error")
        
        # Wait for next minute boundary
        now = datetime.now(timezone.utc)
        next_min = (now + timedelta(minutes=1)).replace(second=5, microsecond=0)
        wait = (next_min - now).total_seconds()
        await asyncio.sleep(max(wait, 1))
    
    await qx.disconnect()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start bot in background when server starts."""
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
        "amount": cfg.AMOUNT
    }

@app.get("/signals")
def signals(limit: int = 50):
    db = SessionLocal()
    rows = db.query(Signal).order_by(Signal.timestamp.desc()).limit(limit).all()
    db.close()
    return [{
        "id": r.id, "time": r.timestamp.isoformat(), "asset": r.asset,
        "prediction": r.prediction, "confidence": r.confidence, "type": r.signal_type
    } for r in rows]

@app.get("/trades")
def trades(limit: int = 50):
    db = SessionLocal()
    rows = db.query(Trade).order_by(Trade.timestamp.desc()).limit(limit).all()
    db.close()
    return [{
        "id": r.id, "time": r.timestamp.isoformat(), "asset": r.asset,
        "direction": r.direction, "amount": r.amount, "result": r.result,
        "profit": r.profit, "paper": r.is_paper
    } for r in rows]

@app.get("/dashboard")
def dashboard():
    return FileResponse("static/dashboard.html")
