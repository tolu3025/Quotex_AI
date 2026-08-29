from typing import List, Dict

def ema(values: List[float], period: int) -> List[float]:
    multiplier = 2 / (period + 1)
    result = []
    for i, price in enumerate(values):
        if i == 0:
            result.append(price)
        else:
            result.append((price - result[-1]) * multiplier + result[-1])
    return result

def rsi(values: List[float], period: int = 14) -> List[float]:
    result = [None] * len(values)
    gains, losses = 0.0, 0.0
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        if i <= period:
            gains += max(change, 0)
            losses += max(-change, 0)
            if i == period:
                avg_gain = gains / period
                avg_loss = losses / period
                if avg_loss == 0:
                    result[i] = 100.0
                else:
                    result[i] = 100 - (100 / (1 + avg_gain / avg_loss))
        else:
            change = values[i] - values[i - 1]
            gain = max(change, 0)
            loss = max(-change, 0)
            avg_gain = (result[i-1] and ((result[i-1] / (100 - result[i-1] + 1e-9)) * 14) or 0)
            # Simplified RSI using previous close-based smoothing
            # Re-calculate properly:
            pass
    # Simpler accurate RSI
    result = [None] * len(values)
    for i in range(period, len(values)):
        gains = [max(values[j] - values[j-1], 0) for j in range(i-period+1, i+1)]
        losses = [max(values[j-1] - values[j], 0) for j in range(i-period+1, i+1)]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100 - (100 / (1 + rs))
    return result

def sma(values: List[float], period: int) -> List[float]:
    result = []
    for i in range(len(values)):
        if i + 1 < period:
            result.append(None)
        else:
            result.append(sum(values[i-period+1:i+1]) / period)
    return result

def std_dev(values: List[float], period: int) -> List[float]:
    result = []
    for i in range(len(values)):
        if i + 1 < period:
            result.append(None)
        else:
            window = values[i-period+1:i+1]
            mean = sum(window) / period
            variance = sum((x - mean) ** 2 for x in window) / period
            result.append(variance ** 0.5)
    return result

def calculate_indicators(candles: List[Dict]) -> Dict:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    opens = [c["open"] for c in candles]
    n = len(closes)

    # EMAs
    ema5 = ema(closes, 5)
    ema10 = ema(closes, 10)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    # RSI
    rsi_vals = rsi(closes, 14)

    # MACD
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [ema12[i] - ema26[i] for i in range(n)]
    macd_signal = ema(macd_line, 9)
    macd_hist = [macd_line[i] - macd_signal[i] for i in range(n)]

    # Bollinger Bands
    bb_middle = sma(closes, 20)
    bb_std = std_dev(closes, 20)
    bb_upper = [bb_middle[i] + 2 * bb_std[i] if bb_middle[i] else None for i in range(n)]
    bb_lower = [bb_middle[i] - 2 * bb_std[i] if bb_middle[i] else None for i in range(n)]

    # ATR
    atr_vals = [None] * n
    for i in range(1, n):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i-1])
        tr3 = abs(lows[i] - closes[i-1])
        tr = max(tr1, tr2, tr3)
        if i == 1:
            atr_vals[i] = tr
        else:
            atr_vals[i] = (atr_vals[i-1] * 13 + tr) / 14 if atr_vals[i-1] else tr

    # Candle features
    bodies = [closes[i] - opens[i] for i in range(n)]
    body_pcts = []
    for i in range(n):
        range_val = highs[i] - lows[i]
        body_pcts.append(abs(bodies[i]) / (range_val + 1e-9))

    trends = ["BULLISH" if closes[i] > ema20[i] else "BEARISH" for i in range(n)]

    return {
        "ema_5": ema5,
        "ema_10": ema10,
        "ema_20": ema20,
        "ema_50": ema50,
        "rsi": rsi_vals,
        "macd": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "atr": atr_vals,
        "body": bodies,
        "body_pct": body_pcts,
        "trend": trends,
    }

def format_prompt_data(candles: List[Dict], asset: str) -> Dict:
    ind = calculate_indicators(candles)
    n = len(candles)
    last = n - 1

    recent = candles[-10:]
    candles_text = []
    for c in recent:
        candles_text.append(
            f"O:{c['open']:.5f} H:{c['high']:.5f} L:{c['low']:.5f} C:{c['close']:.5f}"
        )

    last_3_sum = sum(ind["body"][-3:])
    last_3_dir = "UP" if last_3_sum > 0 else "DOWN"

    def safe_get(arr, idx, fmt=float, digits=5):
        val = arr[idx] if idx < len(arr) else None
        if val is None:
            return None
        return round(val, digits) if fmt == float else val

    return {
        "asset": asset,
        "timeframe": "1 minute",
        "current_price": round(candles[last]["close"], 5),
        "indicators": {
            "rsi": safe_get(ind["rsi"], last),
            "ema_5": safe_get(ind["ema_5"], last),
            "ema_20": safe_get(ind["ema_20"], last),
            "ema_50": safe_get(ind["ema_50"], last),
            "macd": safe_get(ind["macd"], last),
            "macd_signal": safe_get(ind["macd_signal"], last),
            "bb_upper": safe_get(ind["bb_upper"], last),
            "bb_lower": safe_get(ind["bb_lower"], last),
            "atr": safe_get(ind["atr"], last),
            "trend": ind["trend"][last],
        },
        "recent_candles": candles_text,
        "last_3_direction": last_3_dir
    }
