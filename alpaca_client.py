import os
import time
import asyncio
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"
CRYPTO_BASE = "https://data.alpaca.markets/v1beta3/crypto/us"


class AlpacaClient:
    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_SECRET_KEY")
        self.paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"
        self.base = PAPER_BASE if self.paper else LIVE_BASE
        self.headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret
        }
        self.connected = False

    def _get(self, url, params=None):
        r = requests.get(url, headers=self.headers, params=params, timeout=30)
        if r.status_code != 200:
            print(f"[X] API error {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()

    def _post(self, url, json_data):
        r = requests.post(url, headers=self.headers, json=json_data, timeout=30)
        if r.status_code not in (200, 201):
            print(f"[X] API error {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()

    async def connect(self):
        try:
            account = self._get(f"{self.base}/v2/account")
            self.connected = True
            bp = account.get("buying_power", "0")
            print(f"[LINK] Alpaca OK | Paper={self.paper} | Buying Power=${bp}")
            return True
        except Exception as e:
            print(f"[X] Alpaca connect failed: {e}")
            return False

    def is_healthy(self) -> bool:
        return self.connected

    async def reconnect(self):
        return await self.connect()

    def _map_timeframe(self, period: int) -> str:
        mapping = {
            1: "1Min",
            5: "5Min",
            15: "15Min",
            30: "30Min",
            60: "1Hour",
            240: "4Hour",
            1440: "1Day",
        }
        if period in mapping:
            return mapping[period]
        if period <= 1:
            return "1Min"
        elif period < 60:
            return f"{period}Min"
        else:
            return "1Hour"

    def _is_crypto(self, symbol: str) -> bool:
        return "/" in symbol or symbol.upper() in ("BTCUSD", "ETHUSD", "SOLUSD", "LTCUSD", "AVAXUSD", "LINKUSD", "UNIUSD", "AAVEUSD", "MATICUSD", "CRVUSD")

    async def get_candles(self, symbol: str, period: int = 60, count: int = 50):
        try:
            tf = self._map_timeframe(period)
            end = datetime.utcnow()
            start = end - timedelta(minutes=period * count * 4)

            if self._is_crypto(symbol):
                url = f"{CRYPTO_BASE}/bars"
                params = {
                    "symbols": symbol,
                    "timeframe": tf,
                    "start": start.isoformat() + "Z",
                    "end": end.isoformat() + "Z",
                    "limit": min(count * 3, 10000),
                }
            else:
                url = f"{DATA_BASE}/v2/stocks/{symbol}/bars"
                params = {
                    "timeframe": tf,
                    "start": start.isoformat() + "Z",
                    "end": end.isoformat() + "Z",
                    "limit": min(count * 3, 10000),
                    "feed": "iex",
                    "adjustment": "all"
                }

            r = requests.get(url, headers=self.headers, params=params, timeout=30)
            if r.status_code != 200:
                print(f"[X] {symbol}: HTTP {r.status_code} | {r.text[:150]}")
                return []

            data = r.json()

            if self._is_crypto(symbol):
                bars = data.get("bars", {}).get(symbol, [])
            else:
                bars = data.get("bars", [])

            if not bars:
                print(f"[CHART] {symbol}: no bars")
                return []

            candles = []
            for b in bars:
                candles.append({
                    "timestamp": b.get("t"),
                    "open": float(b.get("o", 0)),
                    "high": float(b.get("h", 0)),
                    "low": float(b.get("l", 0)),
                    "close": float(b.get("c", 0)),
                    "volume": int(b.get("v", 0))
                })

            print(f"[CHART] {symbol}: {len(candles)} candles ({tf})")
            return candles[-count:] if len(candles) > count else candles

        except Exception as e:
            print(f"[X] {symbol}: candle error: {e}")
            return []

    async def get_candles_multi(self, symbols: list, period: int = 60, count: int = 50):
        """Fetch multiple crypto symbols in ONE API call (faster)."""
        try:
            tf = self._map_timeframe(period)
            end = datetime.utcnow()
            start = end - timedelta(minutes=period * count * 4)

            crypto_symbols = [s for s in symbols if self._is_crypto(s)]
            if not crypto_symbols:
                # Fall back to individual stock calls
                result = {}
                for s in symbols:
                    result[s] = await self.get_candles(s, period, count)
                return result

            url = f"{CRYPTO_BASE}/bars"
            params = {
                "symbols": ",".join(crypto_symbols),
                "timeframe": tf,
                "start": start.isoformat() + "Z",
                "end": end.isoformat() + "Z",
                "limit": min(count * 3, 10000),
            }

            r = requests.get(url, headers=self.headers, params=params, timeout=30)
            if r.status_code != 200:
                print(f"[X] Multi-crypto HTTP {r.status_code}: {r.text[:150]}")
                return {}

            data = r.json()
            all_bars = data.get("bars", {})

            result = {}
            for sym in symbols:
                bars = all_bars.get(sym, [])
                candles = []
                for b in bars:
                    candles.append({
                        "timestamp": b.get("t"),
                        "open": float(b.get("o", 0)),
                        "high": float(b.get("h", 0)),
                        "low": float(b.get("l", 0)),
                        "close": float(b.get("c", 0)),
                        "volume": int(b.get("v", 0))
                    })
                result[sym] = candles[-count:] if len(candles) > count else candles
                print(f"[CHART] {sym}: {len(result[sym])} candles ({tf}) [batch]")

            return result

        except Exception as e:
            print(f"[X] Multi-candle error: {e}")
            return {}

    async def place_trade(self, symbol: str, amount: float, direction: str, duration: int = 60):
        try:
            side = "buy" if direction == "CALL" else "sell"

            if self._is_crypto(symbol):
                payload = {
                    "symbol": symbol,
                    "notional": str(amount),
                    "side": side,
                    "type": "market",
                    "time_in_force": "gtc"
                }
            else:
                payload = {
                    "symbol": symbol,
                    "notional": str(amount),
                    "side": side,
                    "type": "market",
                    "time_in_force": "day"
                }

            result = self._post(f"{self.base}/v2/orders", payload)
            oid = result.get("id", "unknown")
            print(f"[BUY] {oid} | {side} ${amount} {symbol}")
            return (True, str(oid))
        except Exception as e:
            print(f"[X] Trade error: {e}")
            return (False, str(e))

    async def get_order_status(self, order_id: str):
        try:
            return self._get(f"{self.base}/v2/orders/{order_id}")
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    async def get_balance(self):
        try:
            account = self._get(f"{self.base}/v2/account")
            return float(account.get("buying_power", 0))
        except Exception:
            return None

    async def disconnect(self):
        self.connected = False
        print("[BYE] Alpaca disconnected")
