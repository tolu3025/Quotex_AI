import time
import asyncio
import logging
from pyquotex.stable_api import Quotex
from config import cfg

logger = logging.getLogger(__name__)


def normalize_candles(raw):
    out = []
    if not raw:
        return out
    for c in raw:
        try:
            if isinstance(c, dict):
                out.append({
                    "timestamp": c.get("time") or c.get("timestamp"),
                    "open": float(c.get("open", c.get("o", 0))),
                    "high": float(c.get("high", c.get("h", 0))),
                    "low": float(c.get("low", c.get("l", 0))),
                    "close": float(c.get("close", c.get("c", 0))),
                    "volume": int(c.get("ticks", c.get("volume", c.get("v", 0)))),
                })
            else:
                out.append({
                    "timestamp": getattr(c, "time", None) or getattr(c, "timestamp", None),
                    "open": float(getattr(c, "open", getattr(c, "o", 0))),
                    "high": float(getattr(c, "high", getattr(c, "h", 0))),
                    "low": float(getattr(c, "low", getattr(c, "l", 0))),
                    "close": float(getattr(c, "close", getattr(c, "c", 0))),
                    "volume": int(getattr(c, "ticks", getattr(c, "volume", getattr(c, "v", 0)))),
                })
        except Exception as e:
            logger.warning(f"normalize_candles skip bad candle: {e}")
            continue
    return out


class QuotexClient:
    def __init__(self):
        self.client = Quotex(email=cfg.QUOTEX_EMAIL, password=cfg.QUOTEX_PASSWORD)
        self.connected = False
        self._auth_ok = False
        self._reconnect_attempts = 0

    async def connect(self):
        try:
            print("[⏳] Connecting to Quotex...")
            await asyncio.wait_for(self.client.connect(), timeout=45.0)
            await asyncio.sleep(3)

            if cfg.QUOTEX_DEMO:
                await asyncio.wait_for(self.client.change_account("DEMO"), timeout=10.0)

            bal = await self._get_balance_safe()
            if bal is None:
                print("[❌] Auth failed: balance is None")
                self.connected = False
                self._auth_ok = False
                return False

            self.connected = True
            self._auth_ok = True
            self._reconnect_attempts = 0
            print(f"[🔗] Quotex AUTHENTICATED | DEMO={cfg.QUOTEX_DEMO} | Balance={bal}")
            return True

        except asyncio.TimeoutError:
            print(f"[❌] Quotex connect TIMEOUT after 45s")
            return False
        except Exception as e:
            print(f"[❌] Quotex connect failed: {e}")
            return False

    async def _get_balance_safe(self):
        try:
            return await asyncio.wait_for(self.client.get_balance(), timeout=8.0)
        except Exception:
            return None

    def is_healthy(self) -> bool:
        return self.connected and self._auth_ok

    async def reconnect(self):
        self._reconnect_attempts += 1
        delay = min(60, 5 * self._reconnect_attempts)
        print(f"[🔄] Reconnect attempt {self._reconnect_attempts} in {delay}s...")
        await asyncio.sleep(delay)
        return await self.connect()

    async def get_candles(self, asset: str, period: int = 60, count: int = 100):
        """Fetch candles. Try primary, then fallback. Return whatever we get."""
        if not self.is_healthy():
            return []

        try:
            end_ts = time.time()
            offset = period * count * 3  # Increased offset

            # Primary fetch
            raw = await asyncio.wait_for(
                self.client.get_candles(
                    asset=asset, end_from_time=end_ts, offset=offset, period=period
                ),
                timeout=cfg.CANDLE_TIMEOUT_SEC
            )

            normalized = normalize_candles(raw) if raw else []

            # If primary gave us enough, return it
            if len(normalized) >= count:
                print(f"[📊] {asset}: fetched {len(normalized)} candles (primary)")
                return normalized[-count:]

            # Fallback: historical candles with longer timeout
            print(f"[⏳] {asset}: primary got {len(normalized)}, trying historical...")
            try:
                raw2 = await asyncio.wait_for(
                    self.client.get_historical_candles(
                        asset=asset, amount_of_seconds=offset * 2, period=period, max_workers=1
                    ),
                    timeout=cfg.CANDLE_TIMEOUT_SEC
                )
                if raw2:
                    normalized2 = normalize_candles(raw2)
                    normalized.extend(normalized2)
            except asyncio.TimeoutError:
                print(f"[⚠️] {asset}: historical TIMEOUT")

            # Remove duplicates by timestamp
            seen = set()
            unique = []
            for c in sorted(normalized, key=lambda x: x["timestamp"] or 0):
                ts = c["timestamp"]
                if ts and ts not in seen:
                    seen.add(ts)
                    unique.append(c)

            result = unique[-count:] if len(unique) > count else unique
            print(f"[📊] {asset}: fetched {len(result)} candles (total)")
            return result

        except asyncio.TimeoutError:
            print(f"[❌] {asset}: candle fetch TIMEOUT")
            return []
        except Exception as e:
            print(f"[❌] {asset}: candle fetch error: {e}")
            return []

    async def place_trade(self, asset: str, amount: float, direction: str, duration: int = 60):
        if not self.is_healthy():
            return (False, "Not authenticated")

        try:
            bal = await self._get_balance_safe()
            if bal is not None and bal < amount:
                return (False, "Insufficient balance")

            print(f"[💰] Sending buy: {amount} {asset} {direction.lower()} {duration}s")
            result = await asyncio.wait_for(
                self.client.buy(amount, asset, direction.lower(), duration),
                timeout=cfg.NETWORK_TIMEOUT
            )
            print(f"[💰] Raw response: {result}")
            return result

        except asyncio.TimeoutError:
            return (False, "Trade timeout")
        except Exception as e:
            return (False, str(e))

    async def get_balance(self):
        if not self.is_healthy():
            return None
        return await self._get_balance_safe()

    async def disconnect(self):
        if self.connected:
            try:
                await asyncio.wait_for(self.client.close(), timeout=5.0)
            except Exception:
                pass
            self.connected = False
            self._auth_ok = False
