import os
import time
import httpx
from config import cfg


async def get_prediction(market_data: dict) -> dict:
    """
    Sends market data to OpenAI and gets a directional prediction.
    Falls back to rule-based scoring if OpenAI is unavailable.
    """
    ind = market_data.get("indicators", {})
    price = market_data.get("current_price", 0)
    asset = market_data.get("asset", "UNKNOWN")
    recent = market_data.get("recent_candles", [])
    last_3 = market_data.get("last_3_direction", "FLAT")

    candles_text = "\n".join(recent[-5:]) if recent else "No recent data"

    prompt = (
        "You are a professional quantitative trading analyst. "
        "Analyze the following market data for " + asset +
        " and decide whether to BUY (UP), SELL (DOWN), or HOLD (NO_TRADE).\n\n"
        "Current Price: " + str(price) + "\n"
        "Last 3 candles direction: " + str(last_3) + "\n\n"
        "Technical Indicators:\n"
        "- RSI: " + str(ind.get("rsi", "N/A")) + "\n"
        "- EMA5: " + str(ind.get("ema_5", "N/A")) + " | EMA20: " + str(ind.get("ema_20", "N/A")) + " | EMA50: " + str(ind.get("ema_50", "N/A")) + "\n"
        "- MACD: " + str(ind.get("macd", "N/A")) + " | Signal: " + str(ind.get("macd_signal", "N/A")) + "\n"
        "- BB Upper: " + str(ind.get("bb_upper", "N/A")) + " | BB Lower: " + str(ind.get("bb_lower", "N/A")) + "\n"
        "- ATR: " + str(ind.get("atr", "N/A")) + "\n"
        "- Trend: " + str(ind.get("trend", "N/A")) + "\n\n"
        "Recent candles:\n" + candles_text + "\n\n"
        'Respond ONLY in this exact JSON format:\n'
        '{"prediction": "UP" or "DOWN" or "NO_TRADE", "confidence": 0-100, "reasoning": "short explanation"}'
    )

    api_key = cfg.OPENAI_API_KEY
    if not api_key:
        print("[AI] No OPENAI_API_KEY found, using rule-based fallback")
        return _rule_based_prediction(market_data)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": cfg.OPENAI_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are a precise financial analyst. Respond only in valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 150
                }
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()

            import json
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)
            pred = result.get("prediction", "NO_TRADE").upper()
            conf = float(result.get("confidence", 50))
            reason = result.get("reasoning", "AI analysis")

            if pred not in ("UP", "DOWN", "NO_TRADE"):
                pred = "NO_TRADE"
            conf = max(0, min(100, conf))

            print(f"[AI] {asset}: {pred} @ {conf:.0f}% | {reason[:60]}")
            return {
                "prediction": pred,
                "confidence": round(conf, 2),
                "score": int(conf),
                "reasoning": reason,
                "price_at_signal": round(float(price), 6) if price else 0.0,
                "timestamp": float(time.time()),
                "signal_age_ms": 0.0,
            }

    except Exception as e:
        print(f"[AI] OpenAI failed ({e}), using rule-based fallback")
        return _rule_based_prediction(market_data)


def _rule_based_prediction(market_data: dict) -> dict:
    """Original hardcoded logic as fallback."""
    ind = market_data.get("indicators", {})
    price = market_data.get("current_price", 0)
    signal_time = market_data.get("timestamp", time.time())

    score = 50
    reasons = []

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

    trend = ind.get("trend")
    if trend == "BULLISH":
        score += 10
        reasons.append("Bullish trend")
    elif trend == "BEARISH":
        score -= 10
        reasons.append("Bearish trend")

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

    score = max(0, min(100, int(score)))

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
