import asyncio
import time
from quotex_client import QuotexClient
from feature_engine import format_prompt_data
from openai_predictor import get_prediction
from config import cfg

async def test():
    print("=" * 50)
    print("TESTING BOT COMPONENTS")
    print("=" * 50)
    
    # 1. Test connection
    print("\n[1] Connecting to Quotex...")
    qx = QuotexClient()
    ok = await qx.connect()
    print(f"    Connection: {'OK' if ok else 'FAILED'}")
    if not ok:
        return
    
    # 2. Test candle fetch
    print(f"\n[2] Fetching candles for {cfg.ASSET}...")
    candles = await qx.get_candles(cfg.ASSET, cfg.TIMEFRAME, 100)
    print(f"    Received: {len(candles)} candles")
    if len(candles) > 0:
        print(f"    Latest close: {candles[-1]['close']}")
        print(f"    Sample: {candles[-1]}")
    else:
        print("    ERROR: No candles returned!")
        await qx.disconnect()
        return
    
    # 3. Test feature engine
    print("\n[3] Building features...")
    try:
        data = format_prompt_data(candles, cfg.ASSET)
        print(f"    Current price: {data['current_price']}")
        print(f"    RSI: {data['indicators']['rsi']}")
        print(f"    Trend: {data['indicators']['trend']}")
    except Exception as e:
        print(f"    ERROR: {e}")
        await qx.disconnect()
        return
    
    # 4. Test OpenAI prediction
    print("\n[4] Calling OpenAI...")
    try:
        pred = await get_prediction(data)
        print(f"    Prediction: {pred['prediction']}")
        print(f"    Confidence: {pred['confidence']}%")
        print(f"    Reasoning: {pred['reasoning']}")
    except Exception as e:
        print(f"    ERROR: {e}")
        await qx.disconnect()
        return
    
    # 5. Test trade placement (paper)
    print("\n[5] Testing trade placement...")
    try:
        result = await qx.place_trade(cfg.ASSET, cfg.AMOUNT, "CALL", cfg.TIMEFRAME)
        print(f"    Trade result: {result}")
    except Exception as e:
        print(f"    ERROR: {e}")
    
    await qx.disconnect()
    print("\n" + "=" * 50)
    print("TEST COMPLETE")
    print("=" * 50)

asyncio.run(test())
