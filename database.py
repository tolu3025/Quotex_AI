from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

engine = create_engine(
    "sqlite:///quotex_ai.db",
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Candle(Base):
    __tablename__ = "candles"
    id = Column(Integer, primary_key=True)
    asset = Column(String, index=True)
    timestamp = Column(DateTime)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer, default=0)
    timeframe = Column(Integer)

class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    asset = Column(String)
    prediction = Column(String)
    confidence = Column(Float)
    reasoning = Column(Text)
    signal_type = Column(String)
    indicators_snapshot = Column(Text)

class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    asset = Column(String)
    direction = Column(String)
    amount = Column(Float)
    entry_price = Column(Float)
    exit_price = Column(Float, nullable=True)
    result = Column(String, nullable=True)
    profit = Column(Float, nullable=True)
    is_paper = Column(Boolean, default=True)
    status = Column(String, default="PENDING")

class TrainingSample(Base):
    __tablename__ = "training_data"
    id = Column(Integer, primary_key=True)
    asset = Column(String, index=True)
    timestamp = Column(Integer)
    open_ = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)
    rsi = Column(Float)
    ema5 = Column(Float)
    ema20 = Column(Float)
    ema50 = Column(Float)
    macd = Column(Float)
    macd_signal = Column(Float)
    bb_upper = Column(Float)
    bb_lower = Column(Float)
    atr = Column(Float)
    trend = Column(String)
    next_direction = Column(String)
    next_return = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
