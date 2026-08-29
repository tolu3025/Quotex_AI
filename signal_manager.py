from datetime import datetime, timezone
from database import SessionLocal, Signal, Trade
from config import cfg

class SignalManager:
    def __init__(self):
        self.daily_trades = 0
        self.daily_loss = 0.0
        self.last_reset = datetime.now(timezone.utc).date()

    def _check_reset(self):
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset:
            self.daily_trades = 0
            self.daily_loss = 0.0
            self.last_reset = today

    def classify(self, confidence: float) -> str:
        if confidence < cfg.MIN_CONFIDENCE:
            return "NO_TRADE"
        elif confidence < 80:
            return "WEAK"
        elif confidence < 90:
            return "GOOD"
        return "HIGH"

    def should_trade(self, confidence: float) -> bool:
        self._check_reset()
        if confidence < cfg.MIN_CONFIDENCE:
            return False
        if self.daily_trades >= cfg.MAX_DAILY_TRADES:
            return False
        if self.daily_loss >= cfg.MAX_DAILY_LOSS:
            return False
        return True

    def record_signal(self, prediction, confidence, reasoning, indicators_json) -> int:
        db = SessionLocal()
        sig = Signal(
            asset=cfg.ASSET,
            prediction=prediction,
            confidence=confidence,
            reasoning=reasoning,
            signal_type=self.classify(confidence),
            indicators_snapshot=indicators_json
        )
        db.add(sig)
        db.commit()
        sid = sig.id
        db.close()
        return sid

    def record_trade(self, signal_id, direction, amount, entry_price, is_paper) -> int:
        self._check_reset()
        db = SessionLocal()
        t = Trade(
            signal_id=signal_id,
            asset=cfg.ASSET,
            direction=direction,
            amount=amount,
            entry_price=entry_price,
            is_paper=is_paper,
            status="ACTIVE"
        )
        db.add(t)
        db.commit()
        tid = t.id
        db.close()
        self.daily_trades += 1
        return tid

    def close_trade(self, trade_id, exit_price, result, profit):
        db = SessionLocal()
        t = db.query(Trade).filter(Trade.id == trade_id).first()
        if t:
            t.exit_price = exit_price
            t.result = result
            t.profit = profit
            t.status = "CLOSED"
            db.commit()
            if result == "LOSS" and not t.is_paper:
                self.daily_loss += abs(profit)
        db.close()

