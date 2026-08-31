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

        self.hourly_trades = 0
        self.hourly_reset = datetime.now(timezone.utc)
        self.last_trade_time = 0.0
        self._consecutive_health_failures = 0

        self.daily_profits = 0
        self.daily_profit_reset = datetime.now(timezone.utc).date()

        self.consecutive_losses = 0
        self.consecutive_loss_cooldown_until = 0.0

        self.active_trade_until = 0.0
        self.pending_trades = []

        # NEW: prevent same-asset same-direction re-entry
        self.last_trade_asset = None
        self.last_trade_direction = None
        self.last_trade_confidence = 0.0

    def _check_hourly_reset(self):
        now = datetime.now(timezone.utc)
        if now.hour != self.hourly_reset.hour:
            self.hourly_trades = 0
            self.hourly_reset = now

    def _check_daily_profit_reset(self):
        today = datetime.now(timezone.utc).date()
        if today != self.daily_profit_reset:
            self.daily_profits = 0
            self.daily_profit_reset = today
            SafeLog.info("🌅 NEW DAY — profit counter reset")

    def _can_trade(self) -> tuple[bool, str]:
        self._check_daily_profit_reset()
        if self.daily_profits >= 4:
            return False, "DAILY_PROFIT_TARGET_MET"

        now = time.time()
        if now < self.consecutive_loss_cooldown_until:
            rem = int(self.consecutive_loss_cooldown_until - now)
            return False, f"LOSS_COOLDOWN_{rem}s"

        if now < self.active_trade_until:
            rem = int(self.active_trade_until - now)
            return False, f"TRADE_ACTIVE_{rem}s"

        since_last = now - self.last_trade_time
        if since_last < cfg.TRADE_COOLDOWN_SEC:
            return False, f"GLOBAL_CD_{int(cfg.TRADE_COOLDOWN_SEC - since_last)}s"

        self._check_hourly_reset()
        if self.hourly_trades >= 5:
            return False, "HOURLY_LIMIT"

        if self.manager.daily_loss >= cfg.MAX_DAILY_LOSS:
            return False, "DAILY_LOSS_LIMIT"

        return True, "OK"

    async def _check_pending_trades(self):
        now = time.time()
        for trade in self.pending_trades[:]:
            if now >= trade["expires_at"]:
                result = await self._get_trade_result(trade)
                deal_id_short = str(trade.get("deal_id", "???"))[:20]
                SafeLog.info(f"RESULT | ID:{deal_id_short} | {result}")

                if result == "WIN":
                    self.daily_profits += 1
                    self.consecutive_losses = 0
                    SafeLog.trade(f"🎯 WIN #{self.daily_profits}/4 | Streak reset")
                    if self.daily_profits >= 4:
                        SafeLog.trade("🏆 DAILY TARGET 4/4 — STOPPING UNTIL TOMORROW")
                elif result == "LOSS":
                    self.consecutive_losses += 1
                    SafeLog.warn(f"❌ LOSS | Streak: {self.consecutive_losses}/3")
                    if self.consecutive_losses >= 3:
                        self.consecutive_loss_cooldown_until = now + 600
                        SafeLog.error("🛑 3 CONSECUTIVE LOSSES — 10 MIN COOLDOWN")
                elif result == "TIE":
                    SafeLog.info("🤝 TIE | No change")
                else:
                    SafeLog.warn(f"❓ UNKNOWN result")

                self.pending_trades.remove(trade)

    async def _get_trade_result(self, trade: dict) -> str:
        deal_id = trade.get("deal_id", "")
        asset = trade.get("asset", "")
        direction = trade.get("direction", "")
        entry_price = trade.get("entry_price", 0)

        try:
            if hasattr(self.qx.client, 'check_win') and deal_id:
                r = await asyncio.wait_for(self.qx.client.check_win(deal_id), timeout=10.0)
                if isinstance(r, tuple):
                    return "WIN" if r[0] else "LOSS"
                return "WIN" if r else "LOSS"
        except Exception:
            pass

        try:
            if hasattr(self.qx.client, 'get_trade_result') and deal_id:
                r = await asyncio.wait_for(self.qx.client.get_trade_result(deal_id), timeout=10.0)
                if isinstance(r, dict):
                    profit = r.get("profit", 0)
                    return "WIN" if profit > 0 else "LOSS" if profit < 0 else "TIE"
                return "WIN" if r else "LOSS"
        except Exception:
            pass

        try:
            if asset and entry_price > 0 and direction:
                await asyncio.sleep(2)
                candles = await self.qx.get_candles(asset, cfg.TIMEFRAME, 5)
                if candles and len(candles) > 0:
                    exit_price = candles[-1]["close"]
                    SafeLog.info(f"  PRICE CHECK | entry={entry_price:.5f} exit={exit_price:.5f} dir={direction}")

                    if direction == "PUT":
                        if exit_price < entry_price:
                            return "WIN"
                        elif exit_price > entry_price:
                            return "LOSS"
                        else:
                            return "TIE"
                    elif direction == "CALL":
                        if exit_price > entry_price:
                            return "WIN"
                        elif exit_price < entry_price:
                            return "LOSS"
                        else:
                            return "TIE"
        except Exception as e:
            SafeLog.warn(f"  Price check failed: {e}")

        return "UNKNOWN"

    async def run(self):
        if not await self.qx.connect():
            SafeLog.error("Connection failed. Retrying in 30s...")
            await asyncio.sleep(30)
            if not await self.qx.connect():
                SafeLog.error("Second connect failed. Exiting.")
                return

        self.running = True
        SafeLog.info("=" * 50)
        SafeLog.info("BOT STARTED")
        SafeLog.info(f"Assets: {self.assets}")
        SafeLog.info(f"Paper: {cfg.PAPER_TRADING}")
        SafeLog.info(f"Amount: ₦{cfg.AMOUNT}")
        SafeLog.info(f"Min confidence: {cfg.MIN_CONFIDENCE}%")
        SafeLog.info(f"Daily profit target: 4 wins then STOP")
        SafeLog.info(f"Consecutive loss limit: 3 = 10 min break")
        SafeLog.info(f"No overlap: waits {cfg.TIMEFRAME}s between trades")
        SafeLog.info(f"No repeat: same asset+direction skipped")
        SafeLog.info("=" * 50)

        while self.running:
            cycle_start = time.time()
            await self._check_pending_trades()

            if not self.qx.is_healthy():
                self._consecutive_health_failures += 1
                SafeLog.warn(f"Connection unhealthy (fail #{self._consecutive_health_failures})")
                if self._consecutive_health_failures >= 3:
                    SafeLog.error("Reconnecting...")
                    ok = await self.qx.reconnect()
                    if not ok:
                        SafeLog.error("Reconnect failed. Stopping.")
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
        raw = await self.qx.get_candles(asset, cfg.TIMEFRAME, 50)
        if len(raw) < 30:
            return {"skip": True, "reason": "no_data", "asset": asset}

        try:
            market_data = format_prompt_data(raw, asset)
            if not market_data:
                return {"skip": True, "reason": "indicator_fail", "asset": asset}

            pred = await get_prediction(market_data)
            pred["asset"] = asset
            return pred
        except Exception as e:
            SafeLog.warn(f"{asset} predict error:", e)
            return {"skip": True, "reason": "predict_error", "asset": asset}

    async def _scan_and_trade(self):
        can_trade, reason = self._can_trade()
        if not can_trade:
            if "DAILY_PROFIT" in reason:
                SafeLog.trade(f"🏆 Daily target met — idle until tomorrow")
            elif "LOSS_COOLDOWN" in reason:
                SafeLog.warn(f"🛑 {reason}")
            elif "TRADE_ACTIVE" not in reason:
                SafeLog.info(f"BLOCK: {reason}")
            return

        best_signal = None
        best_asset = None
        best_confidence = 0

        for asset in self.assets:
            res = await self._scan_asset(asset)
            if res.get("skip"):
                continue

            asset_name = res.get("asset")
            conf = res.get("confidence", 0)
            pred = res.get("prediction", "NO_TRADE")
            score = res.get("score", 50)
            SafeLog.info(f"{asset_name}: {pred} @ {conf:.0f}% (score={score})")

            if conf > best_confidence and conf >= cfg.MIN_CONFIDENCE:
                best_confidence = conf
                best_signal = res
                best_asset = asset_name

        SafeLog.info(f"BEST: asset={best_asset} conf={best_confidence}")

        if not best_signal:
            SafeLog.info("No trade this cycle.")
            return

        # === NEW: Skip if same asset + same direction as last trade ===
        pred_dir = best_signal.get("prediction")
        if (best_asset == self.last_trade_asset and 
            pred_dir == self.last_trade_direction):
            SafeLog.warn(f"SKIP: {best_asset} {pred_dir} @ {best_confidence:.0f}% — same as last trade")
            return

        SafeLog.info(f"[🔄] Re-fetching fresh data for {best_asset}...")
        fresh_raw = await self.qx.get_candles(best_asset, cfg.TIMEFRAME, 50)
        if len(fresh_raw) < 30:
            SafeLog.warn(f"{best_asset}: fresh fetch failed")
            return

        try:
            fresh_market = format_prompt_data(fresh_raw, best_asset)
            if not fresh_market:
                return

            fresh_pred = await get_prediction(fresh_market)
            fresh_conf = fresh_pred["confidence"]
            fresh_direction = fresh_pred["prediction"]

            SafeLog.info(f"[🔄] Fresh: {best_asset} {fresh_direction} @ {fresh_conf:.0f}%")

            if fresh_direction != best_signal["prediction"]:
                SafeLog.warn(f"[🔄] FLIPPED: {best_signal['prediction']} → {fresh_direction}. SKIP.")
                return

            if fresh_conf < cfg.MIN_CONFIDENCE:
                SafeLog.warn(f"[🔄] Dropped to {fresh_conf:.0f}%. SKIP.")
                return

            # Double-check same-asset same-direction after fresh fetch
            if (best_asset == self.last_trade_asset and 
                fresh_direction == self.last_trade_direction):
                SafeLog.warn(f"SKIP: {best_asset} {fresh_direction} — same as last trade (fresh confirm)")
                return

            best_signal = fresh_pred
            best_signal["asset"] = best_asset
            best_signal["raw_candles"] = fresh_raw

        except Exception as e:
            SafeLog.error(f"[🔄] Fresh calc error: {e}")
            return

        await self._execute_trade(best_asset, best_signal)

    async def _execute_trade(self, asset: str, pred_data: dict):
        pred = pred_data["prediction"]
        conf = pred_data["confidence"]
        reason = pred_data["reasoning"]
        signal_price = pred_data.get("price_at_signal", 0)

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
        SafeLog.trade(f"PRICE | entry={current_price:.5f}")
        SafeLog.trade("=" * 40)

        now = time.time()
        self.active_trade_until = now + cfg.TIMEFRAME
        self.last_trade_time = now
        self.hourly_trades += 1

        # Remember this trade to prevent repeat
        self.last_trade_asset = asset
        self.last_trade_direction = pred
        self.last_trade_confidence = conf

        if cfg.PAPER_TRADING:
            SafeLog.info(f"PAPER | {direction} {asset} @ {current_price}")
            self.manager.record_signal(pred, conf, reason, "")
            self.pending_trades.append({
                "deal_id": f"PAPER_{int(now)}",
                "asset": asset,
                "direction": direction,
                "entry_price": current_price,
                "placed_at": now,
                "expires_at": now + cfg.TIMEFRAME
            })
            return

        SafeLog.info(f"LIVE | {direction} {asset} @ {current_price} for ₦{cfg.AMOUNT}")
        result = await self.qx.place_trade(asset, cfg.AMOUNT, direction, cfg.TIMEFRAME)

        if result and isinstance(result, tuple) and result[0] is True:
            raw_id = result[1] if len(result) > 1 else int(now)
            if isinstance(raw_id, dict):
                deal_id = str(raw_id.get("id", raw_id.get("deal_id", int(now))))
            else:
                deal_id = str(raw_id)

            SafeLog.trade(f"FILLED | {asset} | {direction} | ID:{deal_id[:25]}")
            self.manager.record_signal(pred, conf, reason, "")
            self.pending_trades.append({
                "deal_id": deal_id,
                "asset": asset,
                "direction": direction,
                "entry_price": current_price,
                "placed_at": now,
                "expires_at": now + cfg.TIMEFRAME
            })
        else:
            SafeLog.error(f"BROKER_REJECT | {asset} | {result}")
            # Clear memory on reject so we can retry
            self.active_trade_until = 0

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
