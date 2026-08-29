import logging
from telegram import Bot
from config import cfg

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.bot = Bot(token=cfg.TELEGRAM_BOT_TOKEN) if cfg.TELEGRAM_BOT_TOKEN else None
        self.chat_id = cfg.TELEGRAM_CHAT_ID

    async def send_signal(self, prediction: str, confidence: float, signal_type: str, reasoning: str):
        if not self.bot:
            return
        emoji = "🟢" if prediction == "UP" else "🔴" if prediction == "DOWN" else "⚪"
        text = (
            f"{emoji} <b>OTC AI SIGNAL</b> {emoji}\n\n"
            f"<b>Asset:</b> {cfg.ASSET}\n"
            f"<b>Timeframe:</b> 1 Minute\n"
            f"<b>Prediction:</b> {prediction}\n"
            f"<b>Confidence:</b> {confidence}%\n"
            f"<b>Strength:</b> {signal_type}\n"
            f"<b>Mode:</b> {'PAPER 📄' if cfg.PAPER_TRADING else 'LIVE ⚠️'}\n\n"
            f"<i>{reasoning}</i>"
        )
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error("Telegram send error: %s", e)

    async def send_result(self, trade_id: int, result: str, profit: float):
        if not self.bot:
            return
        emoji = "✅" if result == "WIN" else "❌"
        text = (
            f"{emoji} <b>Trade Result</b> {emoji}\n\n"
            f"<b>ID:</b> {trade_id}\n"
            f"<b>Result:</b> {result}\n"
            f"<b>P/L:</b> ${profit:.2f}"
        )
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error("Telegram send error: %s", e)
