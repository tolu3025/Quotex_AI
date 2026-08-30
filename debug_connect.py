import asyncio
import time
from pyquotex.stable_api import Quotex
from config import cfg

async def debug():
    print("[🔧] Creating Quotex client...")
    client = Quotex(email=cfg.QUOTEX_EMAIL, password=cfg.QUOTEX_PASSWORD)
    
    print("[⏳] Connecting (60s max)...")
    start = time.time()
    try:
        await asyncio.wait_for(client.connect(), timeout=60.0)
        elapsed = time.time() - start
        print(f"[✅] Connected in {elapsed:.1f}s")
        
        print("[⏳] Getting balance...")
        bal = await asyncio.wait_for(client.get_balance(), timeout=15.0)
        print(f"[💰] Balance: {bal}")
        
        print("[⏳] Testing candle fetch...")
        raw = await asyncio.wait_for(
            client.get_candles(asset="EURUSD_otc", end_from_time=time.time(), offset=300, period=60),
            timeout=15.0
        )
        print(f"[📊] Got {len(raw) if raw else 0} candles")
        
        await client.close()
        print("[✅] FULL SUCCESS")
        
    except asyncio.TimeoutError:
        elapsed = time.time() - start
        print(f"[❌] TIMEOUT after {elapsed:.1f}s")
        print("[💡] The library is hanging. Possible causes:")
        print("    1. session.json exists but is stale")
        print("    2. Quotex API is slow/down")
        print("    3. Your credentials are wrong")
    except Exception as e:
        print(f"[❌] ERROR: {type(e).__name__}: {e}")

asyncio.run(debug())
