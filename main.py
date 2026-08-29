import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from config import cfg
from database import init_db
from feature_engine import format_prompt_data
from openai_predictor import get_prediction
from signal_manager import SignalManager
from quotex_client import QuotexClient
from telegram_bot import TelegramNotifier

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL),
    format="%(asctime)s | %(levelname)s | %(message)s"
)
for noisy in ["pyquotex", "websockets", "urllib3", "httpx"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

class TradingBot:
    def __init__(self):
        init_db()
        self.qx = QuotexClient()
        self.manager = SignalManager()
        self.notifier = TelegramNotifier()
        self.running = False
        self.assets = [a.strip() for a in cfg.ASSET.split(",")]
        
        # Risk limits
        self.asset_cooldown = {}      # Only blocks SAME asset
        self.hourly_trades = 0
        self.hourly_reset = datetime.now(timezone.utc)

    def _check_hourly_reset(self):
        now = datetime.now(timezone.utc)
        if now.hour != self.hourly_reset.hour:
            self.hourly_trades = 0
            self.hourly_reset = now

    def _can_trade(self, asset: str) -> bool:
        now = datetime.now(timezone.utc)
        
        # 1. Same-asset cooldown only (e.g., 10 min before re-trading EURUSD)
        if asset in self.asset_cooldown:
            if now < self.asset_cooldown[asset]:
                return False
        
        # 2. Hourly trade limit across all assets
        self._check_hourly_reset()
        if self.hourly_trades >= 5:
            print(f"[🚫] Hourly trade limit reached ({self.hourly_trades}/5)")
            return False
        
        # 3. Daily loss limit
        if self.manager.daily_loss >= cfg.MAX_DAILY_LOSS:
            print(f"[🛑] DAILY LOSS LIMIT: ₦{self.manager.daily_loss:.0f}")
            return False
        
        return True

    async def run(self):
        if not await self.qx.connect():
            print("[❌] Connection failed.")
            return

        self.running = True
        print(f"\n{'='*50}")
        print(" BOT STARTED")
        print(f" Assets: {self.assets}")
        print(f" Paper: {cfg.PAPER_TRADING}")
        print(f" Amount: ₦{cfg.AMOUNT}")
        print(f" Same-asset cooldown: 10 min")
        print(f" Max trades/hour: 5")
        print(f"{'='*50}\n")

        while self.running:
            try:
                await self._scan_and_trade()
            except Exception as e:
                logging.exception("Cycle error")

            now = datetime.now(timezone.utc)
            next_min = (now + timedelta(minutes=1)).replace(second=5, microsecond=0)
            wait = (next_min - now).total_seconds()
            print(f"[⏳] Next scan in {wait:.0f}s\n")
            await asyncio.sleep(max(wait, 1))

    async def _scan_and_trade(self):
        best_signal = None
        best_asset = None
        best_confidence = 0

        for asset in self.assets:
            # Skip if this specific asset is on cooldown
            if asset in self.asset_cooldown:
                now = datetime.now(timezone.utc)
                if now < self.asset_cooldown[asset]:
                    secs_left = (self.asset_cooldown[asset] - now).seconds
                    print(f"[⏳] {asset}: Cooldown ({secs_left}s left)")
                    continue

            print(f"[🔍] {asset}...")
            raw = await self.qx.get_candles(asset, cfg.TIMEFRAME, 100)
            if len(raw) < 30:
                continue

            try:
                market_data = format_prompt_data(raw, asset)
                pred = await get_prediction(market_data)
                conf = pred["confidence"]
                print(f"    {pred['prediction']} @ {conf:.0f}%")
            except Exception as e:
                print(f"    Error: {e}")
                continue

            if conf > best_confidence and conf >= cfg.MIN_CONFIDENCE:
                best_confidence = conf
                best_signal = pred
                best_asset = asset

        if not best_signal:
            print("[🚫] No trade this cycle.")
            return

        # Final risk check
        if not self._can_trade(best_asset):
            print(f"[🚫] Risk block: {best_asset}")
            return

        await self._execute_trade(best_asset, best_signal)

    async def _execute_trade(self, asset: str, pred_data: dict):
        pred = pred_data["prediction"]
        conf = pred_data["confidence"]
        reason = pred_data["reasoning"]

        print(f"\n{'='*40}")
        print(f"[🎯] {asset} | {pred} | {conf:.0f}%")
        print(f"[🎯] {reason}")
        print(f"{'='*40}")

        direction = "CALL" if pred == "UP" else "PUT"
        raw = await self.qx.get_candles(asset, cfg.TIMEFRAME, 1)
        entry = raw[-1]["close"] if raw else 0

        # Set cooldown ONLY for this asset
        now = datetime.now(timezone.utc)
        self.asset_cooldown[asset] = now + timedelta(minutes=10)
        self.hourly_trades += 1

        if cfg.PAPER_TRADING:
            print(f"[📄] PAPER: {direction} {asset} @ {entry}")
        else:
            print(f"[💰] LIVE: {direction} {asset} @ {entry} for ₦{cfg.AMOUNT}")
            result = await self.qx.place_trade(asset, cfg.AMOUNT, direction, cfg.TIMEFRAME)
            print(f"[💰] Result: {result}")
            
            if result and isinstance(result, tuple) and result[0] is True:
                print("[✅] Trade placed")
            else:
                print("[❌] Trade failed")

    async def stop(self):
        self.running = False
        await self.qx.disconnect()

async def main():
    bot = TradingBot()
    try:
        await bot.run()
    finally:
        await bot.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[🛑] Shutdown.")
