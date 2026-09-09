import os
import json
import base64
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pytz
from config import SPREADSHEET_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def get_client():
    b64 = os.getenv("GOOGLE_CREDENTIALS_B64")
    if not b64:
        raise RuntimeError(
            "Переменная GOOGLE_CREDENTIALS_B64 не найдена. "
            "Добавь её в Railway → Variables."
        )
    try:
        raw = base64.b64decode(b64.strip()).decode("utf-8")
        info = json.loads(raw)
        print(f"Ключ загружен для: {info.get('client_email', '?')}")
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)
    except Exception as e:
        raise RuntimeError(f"Не удалось прочитать GOOGLE_CREDENTIALS_B64: {e}")

def get_spreadsheet():
    client = get_client()
    return client.open_by_key(SPREADSHEET_ID)

def ensure_sheets():
    """Создаёт нужные листы, если их ещё нет"""
    ss = get_spreadsheet()
    existing = [ws.title for ws in ss.worksheets()]

    # Лист Транзакции
    if "Транзакции" not in existing:
        ws = ss.add_worksheet(title="Транзакции", rows=2000, cols=10)
        headers = ["Дата", "Время", "Кто", "Тип", "Категория", "Сумма", "Описание", "Источник", "MessageID"]
        ws.append_row(headers)
        print("Создан лист 'Транзакции'")
    else:
        ws = ss.worksheet("Транзакции")
        # Проверяем заголовки
        if not ws.row_values(1):
            headers = ["Дата", "Время", "Кто", "Тип", "Категория", "Сумма", "Описание", "Источник", "MessageID"]
            ws.append_row(headers)

    # Лист 2026 (сводка) — пока просто создаём пустой, формулы можно добавить позже
    if "2026" not in existing:
        ss.add_worksheet(title="2026", rows=60, cols=15)
        print("Создан лист '2026'")

    return ss

def add_transaction(who: str, category: str, amount: float, description: str = "", source: str = "текст", message_id: str = ""):
    """Добавляет транзакцию в лист Транзакции"""
    ss = get_spreadsheet()
    ws = ss.worksheet("Транзакции")

    moscow = pytz.timezone("Europe/Moscow")
    now = datetime.now(moscow)

    # Определяем тип
    from categories import INCOME_CATEGORIES, SAVINGS_CATEGORIES
    if category in INCOME_CATEGORIES:
        tipo = "Доход"
    elif category in SAVINGS_CATEGORIES:
        tipo = "Сбережения/Инвестиции"
    else:
        tipo = "Расход"

    row = [
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S"),
        who,
        tipo,
        category,
        amount,
        description,
        source,
        str(message_id),
    ]
    ws.append_row(row)
    return row

def get_last_transactions(limit: int = 5):
    """Возвращает последние N транзакций"""
    ss = get_spreadsheet()
    ws = ss.worksheet("Транзакции")
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return []
    data = rows[1:]  # без заголовка
    return data[-limit:]

def delete_last_transaction():
    """Удаляет последнюю строку (кроме заголовка)"""
    ss = get_spreadsheet()
    ws = ss.worksheet("Транзакции")
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return False
    ws.delete_rows(len(rows))
    return True

def get_transactions_for_period(start_date: str, end_date: str):
    """start_date и end_date в формате YYYY-MM-DD"""
    ss = get_spreadsheet()
    ws = ss.worksheet("Транзакции")
    rows = ws.get_all_values()
    if len(rows) <= 1:
        return []

    result = []
    for row in rows[1:]:
        if len(row) < 6:
            continue
        date = row[0]
        if start_date <= date <= end_date:
            result.append(row)
    return result
