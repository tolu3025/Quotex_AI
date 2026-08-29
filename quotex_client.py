import time
import asyncio
import logging
from pyquotex.stable_api import Quotex
from config import cfg

logger = logging.getLogger(__name__)

def normalize_candles(raw):
    out = []
    for c in raw:
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
    return out

class QuotexClient:
    def __init__(self):
        self.client = Quotex(email=cfg.QUOTEX_EMAIL, password=cfg.QUOTEX_PASSWORD)
        self.connected = False
        self._primed_assets = set()

    async def connect(self):
        try:
            await self.client.connect()
            if cfg.QUOTEX_DEMO:
                await self.client.change_account("DEMO")
            self.connected = True
            bal = await self.get_balance()
            print(f"[🔗] Quotex connected | DEMO={cfg.QUOTEX_DEMO} | Balance={bal}")
            
            # Prime once at startup
            await self._prime_price_cache(cfg.ASSET, cfg.TIMEFRAME)
            
            return True
        except Exception as e:
            print(f"[❌] Quotex connect failed: {e}")
            return False

    async def _prime_price_cache(self, asset: str, period: int = 60):
        """Start real-time stream once per asset. Skip if already done."""
        if asset in self._primed_assets:
            return
        
        print(f"[⏳] Priming price stream for {asset}...")
        try:
            await self.client.start_candles_one_stream(asset, period)
        except Exception as e:
            print(f"[⚠️] Stream start warning: {e}")
        
        await asyncio.sleep(3)
        self._primed_assets.add(asset)
        print(f"[✅] Price stream ready for {asset}")

    async def get_candles(self, asset: str, period: int = 60, count: int = 100):
        try:
            end_ts = time.time()
            offset = period * count * 2
            raw = await self.client.get_candles(
                asset=asset, end_from_time=end_ts, offset=offset, period=period
            )
            if not raw or len(raw) < count:
                raw = await self.client.get_historical_candles(
                    asset=asset, amount_of_seconds=offset, period=period, max_workers=2
                )
            normalized = normalize_candles(raw) if raw else []
            return normalized[-count:] if len(normalized) > count else normalized
        except Exception as e:
            print(f"[❌] Candle fetch error: {e}")
            return []

    async def place_trade(self, asset: str, amount: float, direction: str, duration: int = 60):
        try:
            # Stream is already primed at connect, just ensure it's still there
            if asset not in self._primed_assets:
                await self._prime_price_cache(asset, duration)
            
            bal = await self.get_balance()
            if bal is not None and bal < amount:
                print(f"[❌] Insufficient balance: {bal} < {amount}")
                return (False, "Insufficient balance")

            print(f"[💰] Sending buy: {amount} {asset} {direction.lower()} {duration}s")
            result = await self.client.buy(amount, asset, direction.lower(), duration)
            print(f"[💰] Raw response: {result}")
            return result
        except Exception as e:
            print(f"[❌] Trade error: {e}")
            return None

    async def get_balance(self):
        try:
            return await self.client.get_balance()
        except Exception as e:
            print(f"[❌] Balance error: {e}")
            return None

    async def disconnect(self):
        if self.connected:
            await self.client.close()
            self.connected = False
