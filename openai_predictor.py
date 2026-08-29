from config import cfg

async def get_prediction(market_data: dict) -> dict:
    ind = market_data.get("indicators", {})
    
    score = 50
    reasons = []
    
    # RSI
    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi < 30:
            score += 15
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > 70:
            score -= 15
            reasons.append(f"RSI overbought ({rsi:.1f})")
        elif 40 <= rsi <= 60:
            score -= 10  # chop penalty, but doesn't kill everything
            reasons.append(f"RSI chop zone ({rsi:.1f})")
        else:
            # RSI 30-40 or 60-70: mild bias
            if rsi < 45:
                score += 5
            elif rsi > 55:
                score -= 5
    
    # Trend (EMA alignment)
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
        if macd > macd_sig:
            score += 10
            reasons.append("MACD bullish")
        else:
            score -= 10
            reasons.append("MACD bearish")
    
    # EMA stack
    ema5 = ind.get("ema_5")
    ema20 = ind.get("ema_20")
    ema50 = ind.get("ema_50")
    if ema5 and ema20 and ema50:
        if ema5 > ema20 > ema50:
            score += 10
            reasons.append("EMA bullish stack")
        elif ema5 < ema20 < ema50:
            score -= 10
            reasons.append("EMA bearish stack")
    
    # Bollinger position
    bb_up = ind.get("bb_upper")
    bb_low = ind.get("bb_lower")
    price = market_data.get("current_price", 0)
    if bb_up and bb_low and price:
        if price > bb_up:
            score -= 10
            reasons.append("Price above BB upper")
        elif price < bb_low:
            score += 10
            reasons.append("Price below BB lower")
    
    # Clamp
    score = max(0, min(100, score))
    
    # Convert to prediction
    if score >= 70:
        pred = "UP"
        conf = float(score)
    elif score <= 30:
        pred = "DOWN"
        conf = float(100 - score)
    else:
        pred = "NO_TRADE"
        conf = float(score)
    
    return {
        "prediction": pred,
        "confidence": conf,
        "reasoning": " | ".join(reasons) if reasons else "Mixed signals"
    }
