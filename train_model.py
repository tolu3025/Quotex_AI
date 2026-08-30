import sqlite3
import pickle
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "quotex_ai.db")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")

FEATURE_NAMES = [
    "rsi_norm", "ema_ratio", "macd_hist", "macd_hist_slope", "bb_position",
    "bb_width", "bb_squeeze", "atr", "atr_vs_avg", "body_pct", "close_position",
    "wick_ratio", "consec_bull", "consec_bear", "engulfing", "doji", "hammer",
    "dist_ema20", "dist_ema50", "rsi_slope", "trend", "volume_norm"
]

def load_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT rsi, ema5, ema20, ema50, macd, macd_signal,
               bb_upper, bb_lower, atr, trend, next_direction,
               open_, high, low, close, volume
        FROM training_data
        WHERE rsi IS NOT NULL AND macd IS NOT NULL AND ema20 IS NOT NULL
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def build_features(rows):
    X, y = [], []
    for r in rows:
        rsi, ema5, ema20, ema50, macd, macd_sig, bb_up, bb_low, atr, trend, direction, \
        open_, high, low, close, volume = r

        if None in (rsi, ema5, ema20, ema50, macd, macd_sig, bb_up, bb_low, atr, close):
            continue

        trend_val = 1.0 if trend == "BULLISH" else 0.0
        ema_ratio = ema5 / ema20 if ema20 else 1.0
        macd_hist = macd - macd_sig
        bb_width = bb_up - bb_low
        bb_position = (close - bb_low) / (bb_width + 1e-9) if bb_width else 0.5
        bb_squeeze = 1 if bb_width < (close * 0.002) else 0
        
        body_size = abs(close - open_)
        range_ = high - low
        body_pct = body_size / (range_ + 1e-9) if range_ else 0.5
        close_pos = (close - low) / (range_ + 1e-9) if range_ else 0.5
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        wick_ratio = (upper_wick + lower_wick) / (range_ + 1e-9) if range_ else 0.5
        
        dist_ema20 = (close - ema20) / ema20 if ema20 else 0
        dist_ema50 = (close - ema50) / ema50 if ema50 else 0
        
        rsi_norm = rsi / 100.0 if rsi else 0.5
        rsi_slope = 0.0
        macd_hist_slope = 0.0
        atr_vs_avg = 1.0
        consec_bull = 1 if close > open_ else 0
        consec_bear = 1 if close < open_ else 0
        engulfing = 0
        doji = 1 if body_pct < 0.1 else 0
        hammer = 1 if body_pct < 0.3 and lower_wick > 2 * body_size and close < open_ else 0
        volume_norm = min(volume / 1000.0, 5.0) if volume else 0

        # FIXED: use trend_val (float), not trend (string)
        feats = [
            float(rsi_norm), float(ema_ratio), float(macd_hist), float(macd_hist_slope),
            float(bb_position), float(bb_width), float(bb_squeeze), float(atr),
            float(atr_vs_avg), float(body_pct), float(close_pos), float(wick_ratio),
            float(consec_bull), float(consec_bear), float(engulfing), float(doji),
            float(hammer), float(dist_ema20), float(dist_ema50), float(rsi_slope),
            float(trend_val), float(volume_norm)
        ]
        X.append(feats)
        y.append(1 if direction == "UP" else 0)
    return X, y

def train_fallback(X, y):
    print("[FALLBACK] Training pure-Python ensemble...")
    
    n_feats = len(X[0])
    up_sums = [0.0] * n_feats
    down_sums = [0.0] * n_feats
    up_n = down_n = 0

    for feats, label in zip(X, y):
        if label == 1:
            for i, v in enumerate(feats):
                up_sums[i] += v
            up_n += 1
        else:
            for i, v in enumerate(feats):
                down_sums[i] += v
            down_n += 1

    up_means = [s / up_n if up_n else 0 for s in up_sums]
    down_means = [s / down_n if down_n else 0 for s in down_sums]
    weights = [abs(u - d) + 1e-9 for u, d in zip(up_means, down_means)]
    total = sum(weights)
    weights = [w / total for w in weights]

    correct = 0
    for feats, label in zip(X, y):
        up_dist = sum(w * abs(f - m) for w, f, m in zip(weights, feats, up_means))
        down_dist = sum(w * abs(f - m) for w, f, m in zip(weights, feats, down_means))
        pred = 1 if up_dist < down_dist else 0
        if pred == label:
            correct += 1

    acc = correct / len(y) * 100
    print(f"[FALLBACK] Accuracy: {acc:.2f}%")

    model = {
        "_type": "fallback",
        "up_means": up_means,
        "down_means": down_means,
        "weights": weights,
        "features": FEATURE_NAMES
    }
    return model, acc

def main():
    print("=" * 50)
    print(" TRAINING OTC PREDICTOR — RICH FEATURES")
    print("=" * 50)

    rows = load_data()
    print(f"[INFO] Loaded {len(rows)} raw samples")

    X, y = build_features(rows)
    print(f"[INFO] Feature matrix: {len(X)} rows x {len(FEATURE_NAMES)} features")
    print(f"[INFO] UP: {sum(y)} | DOWN: {len(y)-sum(y)}")

    if len(X) < 200:
        print("[ERROR] Need 200+ samples")
        return

    model, acc = train_fallback(X, y)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    print(f"[INFO] Model saved to {MODEL_PATH}")
    print("=" * 50)

if __name__ == "__main__":
    main()
