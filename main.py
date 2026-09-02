import asyncio
import time
import logging
import os
from datetime import datetime, timezone, timedelta

from config import cfg
from database import init_db
from feature_engine import format_prompt_data
from openai_predictor import get_prediction
from signal_manager import SignalManager
from alpaca_client import AlpacaClient
from telegram_bot import TelegramNotifier

# === TRAILING STOP SETTINGS ===
# Set these in your .env or Railway variables, or keep defaults
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "2.0"))   # Close at +2%
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "1.0"))       # Close at -1%
MAX_HOLD_MINUTES = int(os.getenv("MAX_HOLD_MINUTES", "60"))    # Force close after 60 min


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
        self.qx = AlpacaClient()
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

        self.last_trade_asset = None
        self.last_trade_direction = None
        self.last_trade_confidence = 0.0
        self.last_status_time = 0.0

        self.take_profit_pct = TAKE_PROFIT_PCT
        self.stop_loss_pct = STOP_LOSS_PCT
        self.max_hold_minutes = MAX_HOLD_MINUTES

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
            SafeLog.info("NEW DAY — profit counter reset")

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

    def _get_session_info(self) -> tuple[int, str]:
        now = datetime.now(timezone.utc)
        hour = now.hour

        if 0 <= hour < 9:
            return 600, "Tokyo"
        if 8 <= hour < 17:
            return 600, "London"
        if 13 <= hour < 22:
            return 600, "New York"

        return 3600, "OFF-HOURS"

    async def _close_trade(self, trade: dict, current_price: float, result: str):
        """Close a trade, update stats, send Telegram, and remove from pending."""
        asset = trade["asset"]
        deal_id = trade.get("deal_id", "")
        entry_price = trade["entry_price"]
        direction = trade["direction"]

        # Close live position on Alpaca if needed
        if not deal_id.startswith("PAPER_") and not cfg.PAPER_TRADING:
            try:
                close_direction = "PUT" if direction == "CALL" else "CALL"
                await self.qx.place_trade(asset, cfg.AMOUNT, close_direction, cfg.TIMEFRAME)
                SafeLog.info(f"CLOSED | {asset} position on Alpaca")
            except Exception as e:
                SafeLog.warn(f"Close position failed: {e}")

        # Calculate P&L %
        if direction == "CALL":
            pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0
        else:
            pnl_pct = ((entry_price - current_price) / entry_price) * 100 if entry_price else 0

        # Record result
        if result == "WIN":
            self.daily_profits += 1
            self.consecutive_losses = 0
            SafeLog.trade(f"WIN #{self.daily_profits}/4 | +{pnl_pct:.2f}%")
            if self.daily_profits >= 4:
                SafeLog.trade("DAILY TARGET 4/4 — STOPPING UNTIL TOMORROW")
            try:
                asyncio.create_task(self.notifier.send_result(deal_id, "WIN", pnl_pct))
            except Exception as e:
                SafeLog.warn(f"Telegram result failed: {e}")
        elif result == "LOSS":
            self.consecutive_losses += 1
            SafeLog.warn(f"LOSS | Streak: {self.consecutive_losses}/3 | {pnl_pct:.2f}%")
            if self.consecutive_losses >= 3:
                self.consecutive_loss_cooldown_until = time.time() + 600
                SafeLog.error("3 CONSECUTIVE LOSSES — 10 MIN COOLDOWN")
            try:
                asyncio.create_task(self.notifier.send_result(deal_id, "LOSS", pnl_pct))
            except Exception as e:
                SafeLog.warn(f"Telegram result failed: {e}")
        elif result == "TIE":
            SafeLog.info("TIE | No change")
            try:
                asyncio.create_task(self.notifier.send_result(deal_id, "TIE", 0.0))
            except Exception as e:
                SafeLog.warn(f"Telegram result failed: {e}")

        self.pending_trades.remove(trade)
        self.active_trade_until = 0  # Allow new trades immediately

    async def _check_pending_trades(self):
        now = time.time()
        for trade in self.pending_trades[:]:
            asset = trade["asset"]
            direction = trade["direction"]
            entry_price = trade["entry_price"]
            placed_at = trade["placed_at"]

            # Fetch current price
            candles = await self.qx.get_candles(asset, cfg.TIMEFRAME, 5)
            if not candles:
                continue
            current_price = candles[-1]["close"]

            # Calculate unrealized P&L %
            if direction == "CALL":
                pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price else 0
            else:
                pnl_pct = ((entry_price - current_price) / entry_price) * 100 if entry_price else 0

            SafeLog.info(f"  P&L CHECK | {asset} | entry={entry_price:.2f} | current={current_price:.2f} | {pnl_pct:+.2f}%")

            # 1. TAKE PROFIT
            if pnl_pct >= self.take_profit_pct:
                SafeLog.trade(f"TAKE PROFIT TRIGGERED | {asset} | {pnl_pct:.2f}%")
                await self._close_trade(trade, current_price, "WIN")
                continue

            # 2. STOP LOSS
            if pnl_pct <= -self.stop_loss_pct:
                SafeLog.warn(f"STOP LOSS TRIGGERED | {asset} | {pnl_pct:.2f}%")
                await self._close_trade(trade, current_price, "LOSS")
                continue

            # 3. MAX HOLD TIME
            elapsed_min = (now - placed_at) / 60
            if elapsed_min >= self.max_hold_minutes:
                result = "WIN" if pnl_pct > 0 else "LOSS" if pnl_pct < 0 else "TIE"
                SafeLog.info(f"MAX HOLD TIME | {asset} | held {elapsed_min:.0f}min | {pnl_pct:+.2f}% | {result}")
                await self._close_trade(trade, current_price, result)
                continue

    async def _get_trade_result(self, trade: dict) -> str:
        """Legacy fallback — not used by trailing stop logic but kept for compatibility."""
        deal_id = trade.get("deal_id", "")
        asset = trade.get("asset", "")
        direction = trade.get("direction", "")
        entry_price = trade.get("entry_price", 0)

        if deal_id and not deal_id.startswith("PAPER_"):
            try:
                status = await self.qx.get_order_status(deal_id)
                order_status = status.get("status", "").upper()
                filled_avg = status.get("filled_avg_price")
                side = status.get("side", "").lower()

                SafeLog.info(f"  ALPACA ORDER | {deal_id[:20]} | status={order_status} | filled_avg={filled_avg}")

                if order_status in ("FILLED", "CLOSED", "HELD") and filled_avg:
                    candles = await self.qx.get_candles(asset, cfg.TIMEFRAME, 5)
                    if candles:
                        current = candles[-1]["close"]
                        if side == "buy":
                            return "WIN" if current > float(filled_avg) else "LOSS" if current < float(filled_avg) else "TIE"
                        else:
                            return "WIN" if current < float(filled_avg) else "LOSS" if current > float(filled_avg) else "TIE"
            except Exception as e:
                SafeLog.warn(f"  Alpaca order check failed: {e}")

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

    async def _send_status_update(self, best_asset, best_conf, best_pred, reason):
        now = time.time()
        if now - self.last_status_time < 3600:
            return
        self.last_status_time = now

        since_last = int(now - self.last_trade_time) if self.last_trade_time > 0 else 99999
        hours_idle = since_last // 3600
        mins_idle = (since_last % 3600) // 60

        # Count open positions
        open_count = len(self.pending_trades)

        msg = (
            f"⚪ <b>Market Status Update</b> ⚪\n\n"
            f"<b>Assets:</b> {', '.join(self.assets)}\n"
            f"<b>Best Signal:</b> {best_asset or 'None'}\n"
            f"<b>Direction:</b> {best_pred or 'N/A'}\n"
            f"<b>Confidence:</b> {best_conf:.0f}%\n"
            f"<b>Status:</b> {reason}\n"
            f"<b>Open Trades:</b> {open_count}\n\n"
            f"<b>Idle Time:</b> {hours_idle}h {mins_idle}m\n"
            f"<b>Daily Wins:</b> {self.daily_profits}/4\n"
            f"<b>Consecutive Losses:</b> {self.consecutive_losses}/3\n"
            f"<b>TP/SL:</b> +{self.take_profit_pct}% / -{self.stop_loss_pct}%\n"
            f"<b>Mode:</b> {'PAPER 📄' if cfg.PAPER_TRADING else 'LIVE ⚠️'}"
        )

        try:
            if self.notifier and self.notifier.bot:
                await self.notifier.bot.send_message(
                    chat_id=self.notifier.chat_id,
                    text=msg,
                    parse_mode="HTML"
                )
                SafeLog.info("[STATUS] Hourly update sent to Telegram")
        except Exception as e:
            SafeLog.warn(f"[STATUS] Telegram failed: {e}")

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
        SafeLog.info(f"Amount: ${cfg.AMOUNT}")
        SafeLog.info(f"Min confidence: {cfg.MIN_CONFIDENCE}%")
        SafeLog.info(f"Take Profit: +{self.take_profit_pct}%")
        SafeLog.info(f"Stop Loss: -{self.stop_loss_pct}%")
        SafeLog.info(f"Max Hold: {self.max_hold_minutes}min")
        SafeLog.info(f"Daily profit target: 4 wins then STOP")
        SafeLog.info(f"Consecutive loss limit: 3 = 10 min break")
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

            interval, session = self._get_session_info()
            elapsed = time.time() - cycle_start
            sleep_for = max(0.5, interval - elapsed)
            hour = datetime.now(timezone.utc).hour

            if session != "OFF-HOURS":
                SafeLog.info(f"[SESSION] {session} active — next scan in {sleep_for:.0f}s")
            else:
                next_open = ""
                if hour < 8:
                    next_open = f"London opens in {8 - hour}h"
                elif hour < 13:
                    next_open = f"New York opens in {13 - hour}h"
                else:
                    next_open = f"Tokyo opens in {24 - hour}h"
                SafeLog.info(f"[SESSION] Off-hours — {next_open}, scanning in {sleep_for:.0f}s")

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
            _, session = self._get_session_info()
            if "DAILY_PROFIT" in reason:
                SafeLog.trade(f"DAILY TARGET MET — idle until tomorrow")
            elif "LOSS_COOLDOWN" in reason:
                SafeLog.warn(f"STOPPED: {reason}")
            elif "TRADE_ACTIVE" not in reason:
                SafeLog.info(f"BLOCK: {reason} | Session: {session}")
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
            await self._send_status_update(best_asset, best_confidence, None, "No signal >= threshold")
            SafeLog.info("No trade this cycle.")
            return

        pred_dir = best_signal.get("prediction")
        if pred_dir == "DOWN":
            SafeLog.warn(f"SKIP: {best_asset} DOWN — Alpaca crypto cannot short-sell without holding")
            return

        if (best_asset == self.last_trade_asset and 
            pred_dir == self.last_trade_direction and
            time.time() - self.last_trade_time < 300):
            SafeLog.warn(f"SKIP: {best_asset} {pred_dir} — same as last trade (within 5min)")
            return

        SafeLog.info(f"[REFRESH] Re-fetching fresh data for {best_asset}...")
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

            SafeLog.info(f"[REFRESH] Fresh: {best_asset} {fresh_direction} @ {fresh_conf:.0f}%")

            if fresh_direction != best_signal["prediction"]:
                SafeLog.warn(f"[REFRESH] FLIPPED: {best_signal['prediction']} -> {fresh_direction}. SKIP.")
                return

            if fresh_conf < cfg.MIN_CONFIDENCE:
                SafeLog.warn(f"[REFRESH] Dropped to {fresh_conf:.0f}%. SKIP.")
                return

            if (best_asset == self.last_trade_asset and 
                fresh_direction == self.last_trade_direction and
                time.time() - self.last_trade_time < 300):
                SafeLog.warn(f"SKIP: {best_asset} {fresh_direction} — same as last trade (fresh, within 5min)")
                return

            best_signal = fresh_pred
            best_signal["asset"] = best_asset
            best_signal["raw_candles"] = fresh_raw

        except Exception as e:
            SafeLog.error(f"[REFRESH] Fresh calc error: {e}")
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

        try:
            asyncio.create_task(self.notifier.send_signal(pred, conf, "LIVE" if not cfg.PAPER_TRADING else "PAPER", reason))
        except Exception as e:
            SafeLog.warn(f"Telegram signal failed: {e}")

        now = time.time()
        # Block new trades until this one closes (max hold time)
        self.active_trade_until = now + (self.max_hold_minutes * 60)
        self.last_trade_time = now
        self.hourly_trades += 1

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
                "expires_at": now + (self.max_hold_minutes * 60)
            })
            return

        SafeLog.info(f"LIVE | {direction} {asset} @ {current_price} for ${cfg.AMOUNT}")
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
                "expires_at": now + (self.max_hold_minutes * 60)
            })
        else:
            SafeLog.error(f"BROKER_REJECT | {asset} | {result}")
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
