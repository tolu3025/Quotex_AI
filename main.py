import asyncio
import time
import logging
from datetime import datetime, timezone, timedelta

from config import cfg
from database import init_db
from feature_engine import format_prompt_data
from openai_predictor import get_prediction
from signal_manager import SignalManager
from quotex_client import QuotexClient
from telegram_bot import TelegramNotifier


class SafeLog:
    _history = []

    @classmethod
    def _fmt(cls, *parts):
        try:
            out = []
            for p in parts:
                if p is None:
                    out.append("None")
                elif isinstance(p, Exception):
                    out.append(f"ERR:{type(p).__name__}:{str(p)[:60]}")
                elif isinstance(p, float):
                    out.append(f"{p:.4f}")
                else:
                    out.append(str(p)[:120])
            msg = " | ".join(out).replace("\n", " ").replace("\r", "").replace("\x00", "")
            return f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg[:300]}"
        except Exception as e:
            return f"[LOG_FAIL] {str(e)[:80]}"

    @classmethod
    def info(cls, *parts):
        line = cls._fmt(*parts)
        cls._history.append(line)
        print(line)

    @classmethod
    def trade(cls, *parts):
        line = "\033[92m" + cls._fmt(*parts) + "\033[0m"
        cls._history.append(line)
        print(line)

    @classmethod
    def error(cls, *parts):
        line = "\033[91m" + cls._fmt(*parts) + "\033[0m"
        cls._history.append(line)
        print(line)

    @classmethod
    def warn(cls, *parts):
        line = "\033[93m" + cls._fmt(*parts) + "\033[0m"
        cls._history.append(line)
        print(line)

    @classmethod
    def get_history(cls, limit=50):
        return list(cls._history[-limit:])


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

        self.asset_cooldown = {}
        self.hourly_trades = 0
        self.hourly_reset = datetime.now(timezone.utc)
        self.last_trade_time = 0.0
        self._consecutive_health_failures = 0

    def _check_hourly_reset(self):
        now = datetime.now(timezone.utc)
        if now.hour != self.hourly_reset.hour:
            self.hourly_trades = 0
            self.hourly_reset = now

    def _can_trade(self, asset: str) -> bool:
        now = datetime.now(timezone.utc)

        since_last = time.time() - self.last_trade_time
        if since_last < cfg.TRADE_COOLDOWN_SEC:
            return False

        if asset in self.asset_cooldown:
            if now < self.asset_cooldown[asset]:
                return False

        self._check_hourly_reset()
        if self.hourly_trades >= 5:
            SafeLog.info(f"Hourly limit reached ({self.hourly_trades}/5)")
            return False

        if self.manager.daily_loss >= cfg.MAX_DAILY_LOSS:
            SafeLog.error(f"DAILY LOSS LIMIT: ₦{self.manager.daily_loss:.0f}")
            return False

        return True

    async def run(self):
        # Initial connect
        if not await self.qx.connect():
            SafeLog.error("Initial connection failed.")
            SafeLog.error("DELETE session.json and re-login manually:")
            SafeLog.error("  rm ~/quotex-ai/session.json")
            SafeLog.error("  python -c \"from pyquotex.stable_api import Quotex; ...\"")
            return

        self.running = True
        SafeLog.info("=" * 50)
        SafeLog.info("BOT STARTED")
        SafeLog.info(f"Assets: {self.assets}")
        SafeLog.info(f"Paper: {cfg.PAPER_TRADING}")
        SafeLog.info(f"Amount: ₦{cfg.AMOUNT}")
        SafeLog.info(f"Cooldown: {cfg.TRADE_COOLDOWN_SEC}s")
        SafeLog.info(f"Scan interval: {cfg.SCAN_INTERVAL_SEC}s")
        SafeLog.info(f"Signal max age: {cfg.SIGNAL_MAX_AGE_MS}ms")
        SafeLog.info(f"Slippage max: {cfg.SLIPPAGE_MAX_PCT}%")
        SafeLog.info("=" * 50)

        while self.running:
            cycle_start = time.time()

            # === CONNECTION HEALTH CHECK ===
            if not self.qx.is_healthy():
                self._consecutive_health_failures += 1
                SafeLog.warn(f"Connection unhealthy (fail #{self._consecutive_health_failures})")

                if self._consecutive_health_failures >= 3:
                    SafeLog.error("Too many health failures. Trying reconnect...")
                    ok = await self.qx.reconnect()
                    if not ok:
                        SafeLog.error("Reconnect failed. Stopping bot.")
                        SafeLog.error("Fix: rm session.json and re-login.")
                        break
                    self._consecutive_health_failures = 0
                else:
                    await asyncio.sleep(5)
                    continue
            else:
                self._consecutive_health_failures = 0

            try:
                await self._scan_and_trade()
            except Exception as e:
                SafeLog.error("Cycle crash:", e)

            elapsed = time.time() - cycle_start
            sleep_for = max(0.5, cfg.SCAN_INTERVAL_SEC - elapsed)
            await asyncio.sleep(sleep_for)

    async def _scan_asset(self, asset: str) -> dict:
        if asset in self.asset_cooldown:
            now = datetime.now(timezone.utc)
            if now < self.asset_cooldown[asset]:
                secs_left = int((self.asset_cooldown[asset] - now).total_seconds())
                return {"skip": True, "reason": f"cooldown_{secs_left}s", "asset": asset}

        raw = await self.qx.get_candles(asset, cfg.TIMEFRAME, 100)
        if len(raw) < 30:
            return {"skip": True, "reason": "no_data", "asset": asset}

        try:
            market_data = format_prompt_data(raw, asset)
            if not market_data:
                return {"skip": True, "reason": "indicator_fail", "asset": asset}

            pred = await get_prediction(market_data)
            pred["asset"] = asset
            pred["raw_candles"] = raw
            return pred
        except Exception as e:
            SafeLog.warn(f"{asset} predict error:", e)
            return {"skip": True, "reason": "predict_error", "asset": asset}

    async def _scan_and_trade(self):
        tasks = [self._scan_asset(a) for a in self.assets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        best_signal = None
        best_confidence = 0

        for res in results:
            if isinstance(res, Exception):
                SafeLog.error("Asset scan exception:", res)
                continue
            if res.get("skip"):
                if res.get("reason", "").startswith("cooldown"):
                    SafeLog.info(f"{res['asset']}: {res['reason']}")
                continue

            asset = res.get("asset")
            conf = res.get("confidence", 0)
            pred = res.get("prediction", "NO_TRADE")
            SafeLog.info(f"{asset}: {pred} @ {conf:.0f}%")

            if conf > best_confidence and conf >= cfg.MIN_CONFIDENCE:
                best_confidence = conf
                best_signal = res

        if not best_signal:
            SafeLog.info("No trade this cycle.")
            return

        asset = best_signal["asset"]
        if not self._can_trade(asset):
            SafeLog.info(f"Risk block: {asset}")
            return

        await self._execute_trade(asset, best_signal)

    async def _execute_trade(self, asset: str, pred_data: dict):
        pred = pred_data["prediction"]
        conf = pred_data["confidence"]
        reason = pred_data["reasoning"]
        signal_price = pred_data.get("price_at_signal", 0)
        signal_time = pred_data.get("timestamp", time.time())

        # OTC GUARD #1: Signal age
        age_ms = (time.time() - signal_time) * 1000
        if age_ms > cfg.SIGNAL_MAX_AGE_MS:
            SafeLog.warn(f"{asset} REJECTED: signal too old ({age_ms:.0f}ms)")
            return

        # OTC GUARD #2: Slippage
        raw = pred_data.get("raw_candles", [])
        current_price = raw[-1]["close"] if raw else signal_price

        if signal_price > 0 and current_price > 0:
            slippage = abs(current_price - signal_price) / signal_price * 100
            if slippage > cfg.SLIPPAGE_MAX_PCT:
                SafeLog.warn(f"{asset} REJECTED: slippage {slippage:.3f}%")
                return

        direction = "CALL" if pred == "UP" else "PUT"

        SafeLog.trade("=" * 40)
        SafeLog.trade(f"EXEC | {asset} | {pred} | {conf:.0f}%")
        SafeLog.trade(f"REASON | {reason}")
        SafeLog.trade(f"PRICE | signal={signal_price:.5f} current={current_price:.5f} age={age_ms:.0f}ms")
        SafeLog.trade("=" * 40)

        now = datetime.now(timezone.utc)
        self.asset_cooldown[asset] = now + timedelta(minutes=10)
        self.hourly_trades += 1
        self.last_trade_time = time.time()

        if cfg.PAPER_TRADING:
            SafeLog.info(f"PAPER | {direction} {asset} @ {current_price}")
            self.manager.record_signal(pred, conf, reason, "")
            return

        SafeLog.info(f"LIVE | {direction} {asset} @ {current_price} for ₦{cfg.AMOUNT}")
        result = await self.qx.place_trade(asset, cfg.AMOUNT, direction, cfg.TIMEFRAME)

        if result and isinstance(result, tuple) and result[0] is True:
            SafeLog.trade(f"FILLED | {asset} | {direction}")
            self.manager.record_signal(pred, conf, reason, "")
        else:
            SafeLog.error(f"REJECTED_BY_BROKER | {asset} | {result}")

    async def stop(self):
        self.running = False
        await self.qx.disconnect()


async def main():
    bot = TradingBot()
    try:
        await bot.run()
    except KeyboardInterrupt:
        SafeLog.info("Shutdown requested.")
    finally:
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        SafeLog.info("Shutdown.")
