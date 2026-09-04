import base64
import httpx
from config import OPENROUTER_API_KEY, OPENROUTER_VISION_MODEL
from categories import ALL_CATEGORIES

async def analyze_screenshot(image_bytes: bytes) -> dict:
    """
    Отправляет скриншот в OpenRouter и пытается вытащить сумму + категорию.
    Возвращает dict: {"amount": float|None, "category": str|None, "raw": str}
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    categories_list = "\n".join(f"- {c}" for c in ALL_CATEGORIES)

    prompt = f"""Ты помогаешь вести семейный бюджет. 
Проанализируй скриншот из банковского приложения или чека.

Твоя задача:
1. Найти итоговую сумму операции (только число, без валюты).
2. По возможности определить категорию из строгого списка ниже.

Список категорий:
{categories_list}

Ответь СТРОГО в таком формате (без лишнего текста):
СУММА: 1234.56
КАТЕГОРИЯ: Еда магазины
или
СУММА: 1234.56
КАТЕГОРИЯ: не определена

Если сумму найти не удалось — напиши СУММА: не найдена
"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/family-budget-bot",
        "X-Title": "Family Budget Bot",
    }

    payload = {
        "model": OPENROUTER_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 300,
        "temperature": 0.1,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return {"amount": None, "category": None, "raw": f"Ошибка: {e}"}

    # Парсим ответ
    amount = None
    category = None

    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("СУММА:"):
            val = line.split(":", 1)[1].strip().replace(",", ".").replace(" ", "")
            try:
                amount = float(val)
            except ValueError:
                amount = None
        elif line.upper().startswith("КАТЕГОРИЯ:"):
            cat = line.split(":", 1)[1].strip()
            if cat.lower() not in ("не определена", "не найдена", "-"):
                # Ищем точное совпадение или близкое
                for c in ALL_CATEGORIES:
                    if c.lower() == cat.lower() or cat.lower() in c.lower():
                        category = c
                        break
                if not category:
                    category = cat  # оставляем как есть, потом спросим

    return {"amount": amount, "category": category, "raw": text}
