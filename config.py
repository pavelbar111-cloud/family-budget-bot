import os
from dotenv import load_dotenv

load_dotenv()

# === TELEGRAM ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "8611303102:AAE7_49fywvxqD51mfOT3GTb0Lc3ErTnAmQ")

# Разрешённые пользователи (Telegram ID → имя в таблице)
ALLOWED_USERS = {
    467211870: "Паша",
    203570151: "Таня",
}

# ID группы (заполнится автоматически при первом сообщении из группы)
# Можно оставить None — бот сам запомнит
GROUP_CHAT_ID = None

# === GOOGLE SHEETS ===
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1ABLwPgBAJksh6o9pT5TLuqu2OyLMQj9b3SL2jMKNij4")

# === OPENROUTER ===
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "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")

# Бесплатная vision-модель (можно менять)
OPENROUTER_VISION_MODEL = "google/gemma-3-27b-it:free"
# Альтернативы: "meta-llama/llama-4-scout:free", "nvidia/nemotron-nano-12b-v2-vl:free"

# === ВРЕМЯ НАПОМИНАНИЯ ===
REMINDER_HOUR = 17   # 17:00 по Москве
REMINDER_MINUTE = 0
