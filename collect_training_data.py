import asyncio
from database import init_db, SessionLocal, TrainingSample
from feature_engine import calculate_indicators
from quotex_client import QuotexClient

ASSETS = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDUSD_otc", "USDCAD_otc"]
TIMEFRAME = 60

async def collect():
    init_db()
    qx = QuotexClient()
    if not await qx.connect():
        print("[❌] Failed to connect")
        return

    print(f"\n{'='*50}")
    print(" TRAINING DATA COLLECTOR")
    print(f" Assets: {ASSETS}")
    print(" Fetching 100 candles per asset, ONE time each")
    print(f"{'='*50}\n")

    for asset in ASSETS:
        print(f"[📥] {asset}: Fetching candles...")
        
        try:
            raw = await asyncio.wait_for(
                qx.get_candles(asset, TIMEFRAME, 100),
                timeout=20.0
            )
        except asyncio.TimeoutError:
            print(f"[⚠️] {asset}: Timeout, skipping")
            continue

        if len(raw) < 55:
            print(f"[⚠️] {asset}: Only {len(raw)} candles, need 55. Skipping.")
            continue

        print(f"[📥] {asset}: Processing {len(raw)} candles into training samples...")

        collected = 0
        # Sort by timestamp to ensure chronological order
        raw.sort(key=lambda x: int(x["timestamp"]) if isinstance(x["timestamp"], (int, float)) else 0)

        for i in range(50, len(raw) - 1):
            window = raw[i-49:i+1]
            current = raw[i]
            next_candle = raw[i+1]

            try:
                ind = calculate_indicators(window)
                idx = len(window) - 1

                next_return = next_candle["close"] - current["close"]
                next_dir = "UP" if next_return > 0 else "DOWN"

                db = SessionLocal()
                sample = TrainingSample(
                    asset=asset,
                    timestamp=int(current["timestamp"]),
                    open_=float(current["open"]),
                    high=float(current["high"]),
                    low=float(current["low"]),
                    close=float(current["close"]),
                    volume=int(current["volume"]),
                    rsi=ind["rsi"][idx],
                    ema5=ind["ema_5"][idx],
                    ema20=ind["ema_20"][idx],
                    ema50=ind["ema_50"][idx],
                    macd=ind["macd"][idx],
                    macd_signal=ind["macd_signal"][idx],
                    bb_upper=ind["bb_upper"][idx],
                    bb_lower=ind["bb_lower"][idx],
                    atr=ind["atr"][idx],
                    trend=ind["trend"][idx],
                    next_direction=next_dir,
                    next_return=round(next_return, 6)
                )
                db.add(sample)
                db.commit()
                db.close()
                collected += 1

            except Exception as e:
                continue

        print(f"[✅] {asset}: {collected} samples saved")

    await qx.disconnect()

    # Summary
    db = SessionLocal()
    total = db.query(TrainingSample).count()
    ups = db.query(TrainingSample).filter(TrainingSample.next_direction == "UP").count()
    db.close()

    print(f"\n{'='*50}")
    print(" COLLECTION COMPLETE")
    print(f" Total samples in database: {total}")
    if total > 0:
        print(f" UP: {ups} ({ups/total*100:.1f}%) | DOWN: {total-ups} ({(total-ups)/total*100:.1f}%)")
    print(f"{'='*50}")

if __name__ == "__main__":
    asyncio.run(collect())
