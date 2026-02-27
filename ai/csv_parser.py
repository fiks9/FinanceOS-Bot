"""
Smart CSV Parser — автоматична категоризація транзакцій з банківських виписок.

Підтримувані банки (авто-детекція по заголовках):
  1. Monobank       — "Дата і час операції", "MCC", "Деталі операції"
  2. ПриватБанк     — "Дата і час", "Опис операції" / "Деталі операції"
  3. Ощадбанк       — "Дата операції", "Призначення платежу", "Дебет"/"Кредит"
  4. Райффайзен     — "OPERATION DATE", "DOCUMENT AMOUNT" / "Amount" (iBank2 формат)
  5. ПУМБ           — "Дата документу", "Призначення", "Дебет", "Кредит"
  6. A-Банк         — "Дата", "Опис", "Прихід"/"Витрати"
  7. Generic        — fallback для будь-якого CSV

Категоризація:
  Шар 1: MCC-код → точна відповідність
  Шар 2: Ключові слова у виписці (description)
  Fallback: ("Інше", "expense")
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Optional


# ─── MCC → категорія ──────────────────────────────────────────────────────────

MCC_TO_CATEGORY: dict[str, tuple[str, str]] = {
    "5411": ("Супермаркети", "expense"),
    "5412": ("Супермаркети", "expense"),
    "5499": ("Супермаркети", "expense"),
    "5441": ("Кава/Снеки", "expense"),
    "5814": ("Заклади", "expense"),
    "5812": ("Заклади", "expense"),
    "5813": ("Заклади", "expense"),
    "5912": ("Ліки/Лікарі", "expense"),
    "8011": ("Ліки/Лікарі", "expense"),
    "8021": ("Ліки/Лікарі", "expense"),
    "8099": ("Ліки/Лікарі", "expense"),
    "8062": ("Ліки/Лікарі", "expense"),
    "7922": ("Події/Хобі", "expense"),
    "7929": ("Події/Хобі", "expense"),
    "7941": ("Спортзал", "expense"),
    "7997": ("Спортзал", "expense"),
    "5661": ("Одяг/Взуття", "expense"),
    "5691": ("Одяг/Взуття", "expense"),
    "5651": ("Одяг/Взуття", "expense"),
    "5948": ("Одяг/Взуття", "expense"),
    "5732": ("Електроніка", "expense"),
    "5734": ("Сервіси/Підписки", "expense"),
    "5045": ("Електроніка", "expense"),
    "5999": ("Товари для дому", "expense"),
    "5200": ("Товари для дому", "expense"),
    "5251": ("Товари для дому", "expense"),
    "5511": ("Авто", "expense"),
    "5521": ("Авто", "expense"),
    "5541": ("Авто", "expense"),
    "5542": ("Авто", "expense"),
    "7523": ("Авто", "expense"),
    "7538": ("Авто", "expense"),
    "4111": ("Таксі/Громадський", "expense"),
    "4121": ("Таксі/Громадський", "expense"),
    "4131": ("Таксі/Громадський", "expense"),
    "7512": ("Таксі/Громадський", "expense"),
    "4900": ("Оренда/Комунальні", "expense"),
    "4814": ("Зв'язок", "expense"),
    "4812": ("Зв'язок", "expense"),
    "8220": ("Освіта", "expense"),
    "8299": ("Освіта", "expense"),
    "7230": ("Б'юті", "expense"),
    "7011": ("Розважальні підписки", "expense"),
    "6011": ("Переказ (інше)", "transfer"),
    "6012": ("Переказ (інше)", "transfer"),
    "6010": ("Переказ (інше)", "transfer"),
    "6051": ("Обмін валют", "transfer"),
    "6050": ("Обмін валют", "transfer"),
}

# ─── Ключові слова → категорія ───────────────────────────────────────────────

KEYWORD_RULES: list[tuple[list[str], str, str]] = [
    (["сільпо", "silpo", "atb", "атб", "novus", "новус", "metro cash", "auchan", "ашан",
      "fozzy", "фоззі", "велмарт", "велика кишеня", "billa", "білла", "rukavychka",
      "rukavichka", "рукавичка", "ultramarket", "ультрамаркет"],
     "Супермаркети", "expense"),

    (["kfc", "mcdonald", "макдональдс", "burger king", "pizza hut", "pizza",
      "піца", "суші", "sushi", "wok", "ресторан", "кафе", "cafe", "пиво", "beer",
      "pub", "паб", "шаурма", "хінкалі", "puzata hata", "пузата хата", "chelentano",
      "celentano", "vapiano", "mafia", "якитория", "якіторія"],
     "Заклади", "expense"),

    (["starbucks", "coffee", "кава", "lavazza", "снек", "snack", "чай",
      "bakery", "пекарня", "croissant", "круасан", "smoothie"],
     "Кава/Снеки", "expense"),

    (["bolt food", "bolt", "uber", "uklon", "уклон", "taxi", "таксі",
      "маршрутка", "метро", "ukrzaliznytsia", "укрзалізниця", "ryanair",
      "wizz", "wizzair", "турагентство", "booking.com", "airbnb"],
     "Таксі/Громадський", "expense"),

    (["shell", "wog", "okko", "socar", "азс", "brsm", "укрнафта", "бензин",
      "автосервіс", "шиномонтаж", "мийка авто", "автомийка", "парковк",
      "renault", "toyota", "volkswagen"],
     "Авто", "expense"),

    (["київстар", "kyivstar", "vodafone", "life", "лайф", "інтернет",
      "internet", "lifecell", "укртелеком", "mobile", "sim", "тariф"],
     "Зв'язок", "expense"),

    (["комунальні", "комунальна", "водоканал", "газ", "газопостачання",
      "теплоенерго", "електро", "квартплата", "оренда", "аренда",
      "нерухомість", "ЖКП", "ЖКГ"],
     "Оренда/Комунальні", "expense"),

    (["netflix", "spotify", "apple", "google play", "steam", "adobe",
      "microsoft", "subscri", "підписка", "chatgpt", "openai", "claude",
      "anthropic", "midjourney", "figma", "canva", "notion", "patreon"],
     "Сервіси/Підписки", "expense"),

    (["rozetka", "розетка", "foxtrot", "фокстрот", "comfy", "eldorado",
      "ельдорадо", "moyo", "iphone", "samsung", "laptop", "lenovo", "dell",
      "apple store", "xiaomi"],
     "Електроніка", "expense"),

    (["зарплата", "зп", "salary", "аванс", "нарахування зп", "виплата зп",
      "заробітна плата", "заробітня плата"],
     "Зарплата", "income"),

    (["фріланс", "freelance", "upwork", "toptal", "оплата за проект",
      "за послуги", "за роботу", "винагорода"],
     "Фріланс", "income"),

    (["надходження", "нарахування", "зарахування", "від фізичної", "від фоп",
      "повернення коштів", "відшкодування"],
     "Інший дохід", "income"),

    (["подарунок", "gift", "від мами", "від тата", "від батьків"],
     "Подарунок", "income"),

    (["поповнення з картки", "card2card", "p2p", "переказ від", "від:"],
     "Переказ (інше)", "transfer"),

    (["відкладання", "на банку", "накопичення", "заощадження", "до банки",
      "на депозит", "депозит", "pension", "пенсія"],
     "Інвестиції/Скарбничка", "transfer"),

    (["обмін валют", "currency", "exchange", "конверсія", "продаж валюти",
      "купівля валюти", "usd sell", "eur buy"],
     "Обмін валют", "transfer"),

    (["аптека", "pharmacy", "farmacy", "ліки", "лікарня", "клініка",
      "clinic", "лікар", "doctor", "medis", "меділайф"],
     "Ліки/Лікарі", "expense"),

    (["gym", "спортзал", "фітнес", "fitness", "sport club", "SportLife",
      "басейн", "тренажер"],
     "Спортзал", "expense"),

    (["перукар", "barber", "косметика", "salon", "манікюр", "педикюр",
      "краса", "beauty"],
     "Б'юті", "expense"),

    (["zara", "h&m", "bershka", "reserved", "lcwaikiki", "pull&bear",
      "mango", "adidas", "nike", "puma", "одяг", "взуття", "shoes",
      "shafa", "lamoda"],
     "Одяг/Взуття", "expense"),

    (["кіно", "cinema", "multiplex", "планетарій", "театр", "concert",
      "концерт", "квиток", "ticket", "eventbrite", "karabas"],
     "Події/Хобі", "expense"),

    (["зсу", "prytula", "благодійн", "charity", "volunteer", "волонтер",
      "донат", "donat", "ukraine now", "повна чаша", "army"],
     "ЗСУ/Волонтери", "expense"),

    (["комісія банку", "комісія за", "bank fee", "плата за обслуговування",
      "обслуговування рахунку", "смс інформування"],
     "Комісії банків", "expense"),
]


# ─── Визначення банку ────────────────────────────────────────────────────────

class BankFormat:
    MONO = "monobank"
    PRIVAT = "privatbank"
    OSCHADBANK = "oschadbank"
    RAIFFEISEN = "raiffeisen"
    PUMB = "pumb"
    ABANK = "abank"
    GENERIC = "generic"

    LABELS = {
        MONO: "Monobank",
        PRIVAT: "ПриватБанк",
        OSCHADBANK: "Ощадбанк",
        RAIFFEISEN: "Райффайзен Банк",
        PUMB: "ПУМБ",
        ABANK: "A-Банк",
        GENERIC: "Невідомий банк",
    }


def detect_bank(headers: list[str]) -> str:
    """
    Детектує банк за унікальними комбінаціями заголовків CSV.
    Порядок перевірки — від найточнішого до генерика.
    """
    # Нормалізуємо заголовки
    h_lower = {h.lower().strip() for h in headers}
    h_joined = " | ".join(sorted(h_lower))

    # Monobank: унікальна комбінація "дата і час операції" + "деталі операції" + "mcc"
    if "mcc" in h_lower and any("деталі операції" in h for h in h_lower):
        return BankFormat.MONO
    if any("дата і час операції" in h for h in h_lower):
        return BankFormat.MONO

    # ПриватБанк: "дата і час" + "опис операції"
    if any("дата і час" in h for h in h_lower) and any("опис операції" in h for h in h_lower):
        return BankFormat.PRIVAT
    # ПриватБанк старий формат
    if any("деталі операції" in h for h in h_lower) and "категорія" in h_lower:
        return BankFormat.PRIVAT

    # Ощадбанк: "призначення платежу" + ("дебет" або "кредит")
    if any("призначення платежу" in h for h in h_lower) and (
        any("дебет" in h for h in h_lower) or any("кредит" in h for h in h_lower)
    ):
        return BankFormat.OSCHADBANK
    if any("призначення платежу" in h for h in h_lower):
        return BankFormat.OSCHADBANK

    # Райффайзен: "operation date" + "document amount" або "amount"
    if any("operation date" in h for h in h_lower):
        return BankFormat.RAIFFEISEN
    if any("document amount" in h or "операція" in h for h in h_lower) and "amount" in h_lower:
        return BankFormat.RAIFFEISEN

    # ПУМБ: "дата документу" або "дата операції" + "призначення" (без "призначення платежу")
    if any("дата документу" in h for h in h_lower) and any("призначення" in h for h in h_lower):
        return BankFormat.PUMB
    if any("пумб" in h or "pumb" in h for h in h_lower):
        return BankFormat.PUMB

    # A-Банк: "прихід" або "витрати" як окремі колонки
    if any("прихід" in h for h in h_lower) or any("витрати" in h for h in h_lower):
        return BankFormat.ABANK
    if any("abank" in h or "а-банк" in h for h in h_lower):
        return BankFormat.ABANK

    return BankFormat.GENERIC


# ─── Допоміжні парсери полів ─────────────────────────────────────────────────

def _parse_amount(raw: str) -> Optional[float]:
    """'1 500,00' або '-1500.50' → float. None якщо невдало."""
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip().replace("\xa0", "").replace(" ", "")
    # Замінюємо кому на крапку, але тільки якщо вона єдина або остання
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(",") >= 1:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(raw: str) -> Optional[datetime]:
    """Пробує кілька форматів дати, включно з Unix timestamp."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()

    # Unix timestamp (Monobank API)
    if raw.isdigit() and len(raw) >= 9:
        try:
            return datetime.fromtimestamp(int(raw))
        except (ValueError, OSError):
            pass

    formats = [
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d-%m-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _get(row: dict, *keys: str, default: str = "") -> str:
    """Отримує значення з словника по одному або декільком альтернативним ключам."""
    for key in keys:
        for k, v in row.items():
            if k.lower().strip() == key.lower().strip():
                return (v or "").strip()
    return default


# ─── Парсери конкретних банків ────────────────────────────────────────────────

def parse_row_mono(row: dict) -> Optional[dict]:
    """
    Monobank CSV:
    Дата і час операції | Деталі операції | MCC | Сума | Валюта |
    Сума у валюті рахунку | Курс | Комісія | Кешбек | Залишок
    """
    date_raw = _get(row, "Дата і час операції", "date")
    amount_raw = _get(row, "Сума", "Сума у валюті рахунку", "Сума (у валюті рахунку)", "amount")
    desc_raw = _get(row, "Деталі операції", "Опис", "description")
    mcc_raw = _get(row, "MCC", "Mcc", "mcc")

    amount = _parse_amount(amount_raw)
    if amount is None:
        return None
    return {
        "date": _parse_date(date_raw) or datetime.now(),
        "amount": amount,
        "description": desc_raw,
        "mcc": mcc_raw,
    }


def parse_row_privat(row: dict) -> Optional[dict]:
    """
    ПриватБанк CSV (Приват24):
    Дата і час | Картка | Категорія | Опис операції / Деталі операції | Сума | Валюта | Залишок

    Також підтримує старий формат:
    Дата | Час | Деталі операції | Сума | Валюта | Залишок після
    """
    date_raw = _get(row, "Дата і час", "Дата", "Date")
    time_raw = _get(row, "Час", "Time")  # старий формат: окрема колонка часу
    if time_raw and " " not in date_raw:
        date_raw = f"{date_raw} {time_raw}"

    amount_raw = _get(row, "Сума в гривні", "Сума", "Amount", "Сума у валюті")
    desc_raw = _get(row, "Опис операції", "Деталі операції", "Деталі", "Призначення", "Description")
    mcc_raw = _get(row, "MCC", "Mcc")

    amount = _parse_amount(amount_raw)
    if amount is None:
        return None
    return {
        "date": _parse_date(date_raw) or datetime.now(),
        "amount": amount,
        "description": desc_raw,
        "mcc": mcc_raw,
    }


def parse_row_oschadbank(row: dict) -> Optional[dict]:
    """
    Ощадбанк CSV:
    Дата операції | Найменування контрагента | Призначення платежу | Дебет | Кредит | Залишок

    Дебет = витрата (гроші пішли), Кредит = надходження.
    """
    date_raw = _get(row, "Дата операції", "Дата проведення", "Дата", "Date")
    debit_raw = _get(row, "Дебет", "Сума дебету", "Debit")
    credit_raw = _get(row, "Кредит", "Сума кредиту", "Credit")
    desc_raw = _get(row, "Призначення платежу", "Призначення", "Найменування контрагента", "Деталі")

    debit = _parse_amount(debit_raw)
    credit = _parse_amount(credit_raw)

    # Визначаємо суму і напрямок
    if debit and debit > 0:
        amount = -debit  # витрата, повертаємо з мінусом
    elif credit and credit > 0:
        amount = credit  # надходження
    else:
        return None

    return {
        "date": _parse_date(date_raw) or datetime.now(),
        "amount": amount,
        "description": desc_raw,
        "mcc": "",
    }


def parse_row_raiffeisen(row: dict) -> Optional[dict]:
    """
    Райффайзен Банк CSV (iBank2 формат):
    OPERATION DATE | DOCUMENT AMOUNT | TRANSACTION DESCRIPTION | DEBIT/CREDIT FLAG

    Або англійський варіант із полями Amount, Date, Description.
    """
    date_raw = _get(row, "OPERATION DATE", "Operation Date", "Дата операції", "Date")
    amount_raw = _get(row, "DOCUMENT AMOUNT", "Document Amount", "Amount", "Сума", "Сума операції")
    desc_raw = _get(row, "TRANSACTION DESCRIPTION", "Transaction Description",
                     "Призначення", "Description", "Деталі")
    dc_flag = _get(row, "DEBIT/CREDIT", "D/C", "Тип операції", "Type")  # D = debit, C = credit

    amount = _parse_amount(amount_raw)
    if amount is None:
        return None

    # Якщо є явний прапор Debit — робимо суму від'ємною
    if dc_flag.upper().startswith("D"):
        amount = -abs(amount)
    elif dc_flag.upper().startswith("C"):
        amount = abs(amount)

    return {
        "date": _parse_date(date_raw) or datetime.now(),
        "amount": amount,
        "description": desc_raw,
        "mcc": "",
    }


def parse_row_pumb(row: dict) -> Optional[dict]:
    """
    ПУМБ CSV (Digital ПУМБ):
    Дата документу | Найменування контрагента | Призначення | Дебет | Кредит | Залишок

    Або: Дата операції | Опис | Сума дебету | Сума кредиту
    """
    date_raw = _get(row, "Дата документу", "Дата операції", "Дата", "Date")
    debit_raw = _get(row, "Дебет", "Сума дебету", "Debit", "Витрати")
    credit_raw = _get(row, "Кредит", "Сума кредиту", "Credit", "Надходження")
    desc_raw = _get(row, "Призначення", "Призначення платежу",
                     "Найменування контрагента", "Опис", "Description")

    debit = _parse_amount(debit_raw)
    credit = _parse_amount(credit_raw)

    if debit and debit > 0:
        amount = -debit
    elif credit and credit > 0:
        amount = credit
    else:
        return None

    return {
        "date": _parse_date(date_raw) or datetime.now(),
        "amount": amount,
        "description": desc_raw,
        "mcc": "",
    }


def parse_row_abank(row: dict) -> Optional[dict]:
    """
    A-Банк CSV (ABank24):
    Дата | Опис | Прихід | Витрати | Залишок

    Прихід = надходження (кредит), Витрати = списання (дебет).
    """
    date_raw = _get(row, "Дата", "Дата операції", "Дата і час", "Date")
    income_raw = _get(row, "Прихід", "Надходження", "Income", "Зарахування")
    expense_raw = _get(row, "Витрати", "Витрата", "Списання", "Expense")
    desc_raw = _get(row, "Опис", "Деталі", "Призначення", "Description", "Найменування")

    income = _parse_amount(income_raw)
    expense = _parse_amount(expense_raw)

    if income and income > 0:
        amount = income
    elif expense and expense > 0:
        amount = -expense
    else:
        return None

    return {
        "date": _parse_date(date_raw) or datetime.now(),
        "amount": amount,
        "description": desc_raw,
        "mcc": "",
    }


def parse_row_generic(row: dict) -> Optional[dict]:
    """
    Fallback-парсер для будь-якого CSV.
    Шукає колонки по ключовим словам у назвах.
    """
    date, amount, desc, mcc = None, None, "", ""

    for k, v in row.items():
        k_lower = k.lower()
        if date is None and any(x in k_lower for x in ["date", "дата"]):
            date = _parse_date(v)
        if amount is None and any(x in k_lower for x in ["amount", "сума", "sum"]):
            amount = _parse_amount(v)
        if not desc and any(x in k_lower for x in ["desc", "опис", "detail", "reference",
                                                      "note", "призначення", "деталі"]):
            desc = (v or "").strip()
        if not mcc and "mcc" in k_lower:
            mcc = (v or "").strip()

    if amount is None:
        return None
    return {
        "date": date or datetime.now(),
        "amount": amount,
        "description": desc,
        "mcc": mcc,
    }


# ─── Категоризатор ────────────────────────────────────────────────────────────

def categorize(description: str, mcc: str) -> tuple[str, str, bool]:
    """
    Визначає (category_name, tx_type, ignore_in_stats).
    Шар 1: MCC → точна відповідність.
    Шар 2: ключові слова в описі.
    Fallback: ("Інше", "expense", False).
    """
    if mcc and mcc.strip() in MCC_TO_CATEGORY:
        cat_name, tx_type = MCC_TO_CATEGORY[mcc.strip()]
        return cat_name, tx_type, False

    desc_lower = description.lower()
    for keywords, cat_name, tx_type in KEYWORD_RULES:
        if any(kw in desc_lower for kw in keywords):
            ignore = any(kw in desc_lower for kw in ["повернення боргу", "поділ", "split check"])
            return cat_name, tx_type, ignore

    return "Інше", "expense", False


def find_category_id(categories: list[dict], name: str, tx_type: str) -> Optional[str]:
    """Fuzzy пошук category_id."""
    name_q = name.lower().strip()
    same_type = [c for c in categories if c.get("type") == tx_type]

    for cat in same_type:
        if cat.get("name", "").lower().strip() == name_q:
            return cat["id"]
    for cat in same_type:
        db_name = cat.get("name", "").lower().strip()
        if name_q in db_name or db_name in name_q:
            return cat["id"]

    q_tokens = set(name_q.replace("/", " ").split())
    best_id, best_score = None, 0
    for cat in same_type:
        db_tokens = set(cat.get("name", "").lower().replace("/", " ").split())
        score = len(q_tokens & db_tokens)
        if score > best_score:
            best_score, best_id = score, cat["id"]

    return best_id if best_score > 0 else None


# ─── Головна функція ──────────────────────────────────────────────────────────

class ParseResult:
    def __init__(self, rows: list[dict], skipped: int, bank: str):
        self.rows = rows
        self.skipped = skipped
        self.bank = bank


def parse_csv(content: bytes, user_id: str, categories: list[dict]) -> ParseResult:
    """
    Парсить CSV, детектує банк, нормалізує транзакції та категоризує їх.
    Повертає готові dict для bulk_insert_transactions.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        return ParseResult([], 0, BankFormat.GENERIC)

    bank = detect_bank(list(reader.fieldnames))

    # Вибираємо відповідний парсер рядків
    parser_map = {
        BankFormat.MONO: parse_row_mono,
        BankFormat.PRIVAT: parse_row_privat,
        BankFormat.OSCHADBANK: parse_row_oschadbank,
        BankFormat.RAIFFEISEN: parse_row_raiffeisen,
        BankFormat.PUMB: parse_row_pumb,
        BankFormat.ABANK: parse_row_abank,
        BankFormat.GENERIC: parse_row_generic,
    }
    parse_row = parser_map[bank]

    rows: list[dict] = []
    skipped = 0

    for raw_row in reader:
        parsed = parse_row(raw_row)
        if parsed is None:
            skipped += 1
            continue

        raw_amount: float = parsed["amount"]
        abs_amount = abs(raw_amount)

        if abs_amount < 0.01:
            skipped += 1
            continue

        # Знак визначає напрямок: від'ємна = витрата, додатня = надходження/переказ
        is_debit = raw_amount < 0

        cat_name, tx_type, ignore = categorize(parsed["description"], parsed["mcc"])

        # Додаткова корекція знаку для банків з роздільними Дебет/Кредит колонками
        if is_debit and tx_type == "income":
            # Помилка категоризатора: опис схожий на дохід але сума від'ємна → expense
            tx_type = "expense"
        if not is_debit and tx_type == "expense":
            # Позитивна сума із expense-описом → скоріш за все переказ або надходження
            desc_lower = parsed["description"].lower()
            is_income_kw = any(kw in desc_lower for kw in [
                "зарплата", "зп", "salary", "аванс", "нарахування зп", "від фізичної"
            ])
            tx_type = "income" if is_income_kw else "transfer"
            cat_name = "Інший дохід" if is_income_kw else "Переказ (інше)"

        category_id = find_category_id(categories, cat_name, tx_type)

        rows.append({
            "user_id": user_id,
            "amount": abs_amount,
            "type": tx_type,
            "category_id": category_id,
            "description": (parsed["description"] or "")[:255] or None,
            "source": "csv",
            "ignore_in_stats": ignore,
            "transaction_date": parsed["date"].isoformat(),
            "metadata": {"raw_category": cat_name, "mcc": parsed["mcc"], "bank": bank},
        })

    return ParseResult(rows, skipped, bank)
