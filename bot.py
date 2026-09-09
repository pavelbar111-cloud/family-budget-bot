import logging
import re
from datetime import time, datetime, timedelta
import pytz

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, JobQueue
)

from config import BOT_TOKEN, ALLOWED_USERS, REMINDER_HOUR, REMINDER_MINUTE
from categories import ALL_CATEGORIES, CATEGORY_KEYWORDS, INCOME_CATEGORIES, SAVINGS_CATEGORIES, EXPENSE_CATEGORIES
import sheets
from vision import analyze_screenshot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Временное хранилище ожидающих подтверждения (user_id → данные)
pending = {}

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

def get_who(user_id: int) -> str:
    return ALLOWED_USERS.get(user_id, "Неизвестный")

def parse_text_expense(text: str):
    """
    Пытается разобрать текст вида:
    "Еда магазины 4500"
    "4500 еда магазины"
    "Паша ЗП 136000"
    """
    text = text.strip().lower()
    # Ищем число
    amount_match = re.search(r"(\d+[.,]?\d*)", text.replace(" ", ""))
    if not amount_match:
        # Пробуем найти число с пробелами (1 200)
        amount_match = re.search(r"(\d{1,3}(?:\s\d{3})*(?:[.,]\d+)?)", text)
    
    amount = None
    if amount_match:
        raw = amount_match.group(1).replace(" ", "").replace(",", ".")
        try:
            amount = float(raw)
        except:
            pass

    # Ищем категорию по ключевым словам
    category = None
    best_len = 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text and len(kw) > best_len:
                category = cat
                best_len = len(kw)

    # Если не нашли по ключевым словам — пробуем точное совпадение названия
    if not category:
        for cat in ALL_CATEGORIES:
            if cat.lower() in text:
                category = cat
                break

    return amount, category

def make_category_keyboard(prefix: str = "cat"):
    """Клавиатура с категориями (разбиваем на группы)"""
    buttons = []
    row = []
    for i, cat in enumerate(ALL_CATEGORIES):
        row.append(InlineKeyboardButton(cat, callback_data=f"{prefix}:{cat}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def make_confirm_keyboard(amount, category):
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, записать", callback_data=f"confirm:{amount}:{category}"),
            InlineKeyboardButton("✏️ Другая категория", callback_data=f"change_cat:{amount}"),
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== ОБРАБОТЧИКИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Извини, у тебя нет доступа к этому боту.")
        return

    text = (
        f"Привет, {get_who(user.id)}!\n\n"
        "Я бот для учёта семейного бюджета.\n\n"
        "Как пользоваться:\n"
        "• Просто напиши: <code>Еда магазины 4500</code>\n"
        "• Или: <code>Паша ЗП 136000</code>\n"
        "• Можно кидать скриншоты из банка\n"
        "• /отчет — сводка за текущий месяц\n"
        "• /удалить — удалить последнюю запись\n"
        "• /помощь — список команд"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    text = (
        "<b>Команды:</b>\n"
        "/start — начать\n"
        "/отчет — отчёт за текущий месяц\n"
        "/отчет_прошлый — прошлый месяц\n"
        "/отчет_квартал — текущий квартал\n"
        "/отчет_год — текущий год\n"
        "/удалить — удалить последнюю запись\n"
        "/помощь — эта справка\n\n"
        "<b>Примеры сообщений:</b>\n"
        "Еда магазины 4850\n"
        "Паша ЗП 136000\n"
        "Ипотека 36110\n"
        "Такси 650"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        return

    text = update.message.text.strip()
    text_lower = text.lower()
    who = get_who(user.id)

    # --- Сначала проверяем команды отчётов и удаления (на случай если CommandHandler не сработал) ---
    if text_lower in ("отчет", "отчёт", "report", "/отчет", "/отчёт", "/report"):
        await report_month(update, context)
        return
    if text_lower in ("отчет_прошлый", "отчёт_прошлый", "прошлый", "/отчет_прошлый", "/report_prev"):
        await report_prev_month(update, context)
        return
    if text_lower in ("отчет_квартал", "отчёт_квартал", "квартал", "/отчет_квартал"):
        await report_quarter(update, context)
        return
    if text_lower in ("отчет_год", "отчёт_год", "год", "/отчет_год"):
        await report_year(update, context)
        return
    if text_lower in ("удалить", "delete", "/удалить", "/delete"):
        await delete_last(update, context)
        return
    if text_lower in ("помощь", "help", "/помощь", "/help"):
        await help_command(update, context)
        return

    # --- Обычный разбор расхода/дохода ---
    amount, category = parse_text_expense(text)

    if amount is None:
        await update.message.reply_text(
            "Не смог понять сумму.\n"
            "Напиши в формате:\n"
            "<code>Еда магазины 4500</code>\n"
            "или\n"
            "<code>4500 продукты</code>\n\n"
            "Или команды: <b>отчет</b>, <b>удалить</b>, <b>помощь</b>",
            parse_mode="HTML"
        )
        return

    if category is None:
        # Сохраняем сумму и просим выбрать категорию
        pending[user.id] = {"amount": amount, "who": who, "source": "текст", "message_id": update.message.message_id}
        await update.message.reply_text(
            f"Сумма: <b>{amount:,.0f} ₽</b>\nВыбери категорию:",
            parse_mode="HTML",
            reply_markup=make_category_keyboard("cat")
        )
        return

    # Всё понятно — подтверждаем
    pending[user.id] = {
        "amount": amount,
        "category": category,
        "who": who,
        "source": "текст",
        "message_id": update.message.message_id
    }
    await update.message.reply_text(
        f"Записать?\n\n"
        f"Кто: <b>{who}</b>\n"
        f"Категория: <b>{category}</b>\n"
        f"Сумма: <b>{amount:,.0f} ₽</b>",
        parse_mode="HTML",
        reply_markup=make_confirm_keyboard(amount, category)
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        return

    who = get_who(user.id)
    await update.message.reply_text("🔍 Смотрю скриншот...")

    photo = update.message.photo[-1]  # самое большое
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()

    result = await analyze_screenshot(bytes(image_bytes))
    amount = result["amount"]
    category = result["category"]

    if amount is None:
        pending[user.id] = {"who": who, "source": "скрин", "message_id": update.message.message_id}
        await update.message.reply_text(
            "Не удалось надёжно вытащить сумму со скрина.\n"
            "Напиши сумму числом (например 4500), а потом выберешь категорию."
        )
        return

    if category and category in ALL_CATEGORIES:
        pending[user.id] = {
            "amount": amount,
            "category": category,
            "who": who,
            "source": "скрин",
            "message_id": update.message.message_id
        }
        await update.message.reply_text(
            f"Нашёл на скрине:\n\n"
            f"Сумма: <b>{amount:,.0f} ₽</b>\n"
            f"Категория: <b>{category}</b>\n\n"
            f"Записать?",
            parse_mode="HTML",
            reply_markup=make_confirm_keyboard(amount, category)
        )
    else:
        pending[user.id] = {
            "amount": amount,
            "who": who,
            "source": "скрин",
            "message_id": update.message.message_id
        }
        await update.message.reply_text(
            f"Сумма: <b>{amount:,.0f} ₽</b>\n"
            f"Категорию определить не удалось.\nВыбери категорию:",
            parse_mode="HTML",
            reply_markup=make_category_keyboard("cat")
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    if not is_allowed(user.id):
        return

    data = query.data

    if data == "cancel":
        pending.pop(user.id, None)
        await query.edit_message_text("Отменено.")
        return

    if data.startswith("cat:"):
        category = data.split(":", 1)[1]
        info = pending.get(user.id)
        if not info or "amount" not in info:
            await query.edit_message_text("Сессия устарела. Пришли данные заново.")
            return

        amount = info["amount"]
        who = info["who"]
        source = info.get("source", "текст")
        msg_id = info.get("message_id", "")

        try:
            sheets.add_transaction(who, category, amount, source=source, message_id=msg_id)
            pending.pop(user.id, None)
            await query.edit_message_text(
                f"✅ Записано!\n\n"
                f"{who} → <b>{category}</b>\n"
                f"<b>{amount:,.0f} ₽</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(e)
            await query.edit_message_text(f"Ошибка записи в таблицу: {e}")
        return

    if data.startswith("confirm:"):
        parts = data.split(":")
        amount = float(parts[1])
        category = parts[2]
        info = pending.get(user.id, {})
        who = info.get("who", get_who(user.id))
        source = info.get("source", "текст")
        msg_id = info.get("message_id", "")

        try:
            sheets.add_transaction(who, category, amount, source=source, message_id=msg_id)
            pending.pop(user.id, None)
            await query.edit_message_text(
                f"✅ Записано!\n\n"
                f"{who} → <b>{category}</b>\n"
                f"<b>{amount:,.0f} ₽</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(e)
            await query.edit_message_text(f"Ошибка записи: {e}")
        return

    if data.startswith("change_cat:"):
        amount = float(data.split(":")[1])
        info = pending.get(user.id, {})
        info["amount"] = amount
        pending[user.id] = info
        await query.edit_message_text(
            f"Сумма: <b>{amount:,.0f} ₽</b>\nВыбери категорию:",
            parse_mode="HTML",
            reply_markup=make_category_keyboard("cat")
        )
        return

async def delete_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    ok = sheets.delete_last_transaction()
    if ok:
        await update.message.reply_text("✅ Последняя запись удалена.")
    else:
        await update.message.reply_text("Нечего удалять.")

async def report_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    moscow = pytz.timezone("Europe/Moscow")
    now = datetime.now(moscow)
    start = now.replace(day=1).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    await send_report(update, start, end, f"Текущий месяц ({now.strftime('%B %Y')})")

async def report_prev_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    moscow = pytz.timezone("Europe/Moscow")
    now = datetime.now(moscow)
    first = now.replace(day=1)
    prev_end = first - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    await send_report(update, prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d"),
                      f"Прошлый месяц ({prev_start.strftime('%B %Y')})")

async def report_quarter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    moscow = pytz.timezone("Europe/Moscow")
    now = datetime.now(moscow)
    quarter = (now.month - 1) // 3
    start_month = quarter * 3 + 1
    start = now.replace(month=start_month, day=1)
    await send_report(update, start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"),
                      f"Текущий квартал")

async def report_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    moscow = pytz.timezone("Europe/Moscow")
    now = datetime.now(moscow)
    start = now.replace(month=1, day=1)
    await send_report(update, start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"),
                      f"Текущий год {now.year}")

async def send_report(update: Update, start: str, end: str, title: str):
    rows = sheets.get_transactions_for_period(start, end)
    if not rows:
        await update.message.reply_text(f"Нет данных за период «{title}».")
        return

    income = 0.0
    expense = 0.0
    by_cat = {}

    for row in rows:
        try:
            amount = float(str(row[5]).replace(",", ".").replace(" ", ""))
            cat = row[4]
            tipo = row[3]
            if tipo == "Доход":
                income += amount
            else:
                expense += amount
            by_cat[cat] = by_cat.get(cat, 0) + amount
        except:
            continue

    saldo = income - expense

    text = f"<b>{title}</b>\n"
    text += f"Период: {start} — {end}\n\n"
    text += f"📥 Доходы: <b>{income:,.0f} ₽</b>\n"
    text += f"📤 Расходы: <b>{expense:,.0f} ₽</b>\n"
    text += f"💰 Сальдо: <b>{saldo:,.0f} ₽</b>\n\n"
    text += "<b>По категориям:</b>\n"

    for cat, val in sorted(by_cat.items(), key=lambda x: -abs(x[1])):
        text += f"• {cat}: {val:,.0f} ₽\n"

    await update.message.reply_text(text, parse_mode="HTML")

async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневное напоминание в 17:00 по Москве"""
    # Отправляем всем разрешённым пользователям в личку + пробуем в группу если знаем
    text = (
        "⏰ Напоминание!\n\n"
        "Ты сегодня отправил отчёт по расходам?\n"
        "Если ещё нет — самое время."
    )
    for user_id in ALLOWED_USERS:
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание {user_id}: {e}")

def main():
    # Создаём листы при старте (не роняем бота, если таблица временно недоступна)
    try:
        sheets.ensure_sheets()
        print("Google Sheets готовы")
    except Exception as e:
        print(f"Ошибка при подготовке таблицы: {e}")
        print("Проверь service_account.json и права доступа. Бот всё равно запустится.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды (только латиница — Telegram не принимает кириллицу в CommandHandler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("delete", delete_last))
    app.add_handler(CommandHandler("report", report_month))
    app.add_handler(CommandHandler("report_prev", report_prev_month))
    app.add_handler(CommandHandler("report_quarter", report_quarter))
    app.add_handler(CommandHandler("report_year", report_year))

    # Сообщения
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Кнопки
    app.add_handler(CallbackQueryHandler(button_handler))

    # Напоминание каждый день в 17:00 по Москве
    moscow = pytz.timezone("Europe/Moscow")
    reminder_time = time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, tzinfo=moscow)
    app.job_queue.run_daily(daily_reminder, time=reminder_time)

    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
