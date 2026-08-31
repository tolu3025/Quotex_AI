import time
from config import cfg


async def get_prediction(market_data: dict) -> dict:
    ind = market_data.get("indicators", {})
    price = market_data.get("current_price", 0)
    signal_time = market_data.get("timestamp", time.time())

    score = 50
    reasons = []

    # RSI
    rsi = ind.get("rsi")
    if rsi is not None:
        try:
            rsi = float(rsi)
            if rsi < 30:
                score += 15
                reasons.append(f"RSI oversold ({rsi:.1f})")
            elif rsi > 70:
                score -= 15
                reasons.append(f"RSI overbought ({rsi:.1f})")
            elif 40 <= rsi <= 60:
                score -= 10
                reasons.append(f"RSI chop ({rsi:.1f})")
            else:
                if rsi < 45:
                    score += 5
                elif rsi > 55:
                    score -= 5
        except (TypeError, ValueError):
            pass

    # Trend
    trend = ind.get("trend")
    if trend == "BULLISH":
        score += 10
        reasons.append("Bullish trend")
    elif trend == "BEARISH":
        score -= 10
        reasons.append("Bearish trend")

    # MACD
    macd = ind.get("macd")
    macd_sig = ind.get("macd_signal")
    if macd is not None and macd_sig is not None:
        try:
            if float(macd) > float(macd_sig):
                score += 10
                reasons.append("MACD bullish")
            else:
                score -= 10
                reasons.append("MACD bearish")
        except (TypeError, ValueError):
            pass

    # EMA alignment
    ema5 = ind.get("ema_5")
    ema20 = ind.get("ema_20")
    ema50 = ind.get("ema_50")
    if ema5 is not None and ema20 is not None and ema50 is not None:
        try:
            e5, e20, e50 = float(ema5), float(ema20), float(ema50)
            if e5 > e20 > e50:
                score += 10
                reasons.append("EMA bullish stack")
            elif e5 < e20 < e50:
                score -= 10
                reasons.append("EMA bearish stack")
        except (TypeError, ValueError):
            pass

    # Bollinger
    bb_up = ind.get("bb_upper")
    bb_low = ind.get("bb_lower")
    if bb_up is not None and bb_low is not None and price:
        try:
            bu, bl = float(bb_up), float(bb_low)
            if price > bu:
                score -= 10
                reasons.append("Above BB upper")
            elif price < bl:
                score += 10
                reasons.append("Below BB lower")
        except (TypeError, ValueError):
            pass

    # Clamp
    score = max(0, min(100, int(score)))

    # === THRESHOLDS: 80% minimum confidence ===
    # UP: score >= 80 gives conf = 80-100%
    # DOWN: score <= 20 gives conf = 80-100%
    if score >= 80:
        pred = "UP"
        conf = float(score)
    elif score <= 20:
        pred = "DOWN"
        conf = float(100 - score)
    else:
        pred = "NO_TRADE"
        conf = float(score)

    return {
        "prediction": str(pred),
        "confidence": round(float(conf), 2),
        "score": int(score),
        "reasoning": " | ".join(reasons) if reasons else "Mixed signals",
        "price_at_signal": round(float(price), 6) if price else 0.0,
        "timestamp": float(signal_time),
        "signal_age_ms": 0.0,
    }
