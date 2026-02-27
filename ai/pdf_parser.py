"""
PDF Parser для банківських виписок — pdfplumber-based.

Підтримувані банки:
  1. A-Банк  
  2. Monobank 
  3. Generic — fallback

Ключові правила класифікації:
  • від'ємна сума (col[4] починається з '-') → EXPENSE або TRANSFER_OUT
  • позитивна сума                           → INCOME (зарахування)

Шапка першої сторінки A-Bank містить точні підсумки:
  "Сума витрат за період: X UAH"
  "Сума зарахувань за період: X UAH"
  → парсимо і передаємо у ParseResult як bank_totals
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Optional

import pdfplumber

from ai.csv_parser import (
    BankFormat,
    ParseResult,
    _parse_amount,
    _parse_date,
    categorize,
    find_category_id,
)


# ─── ParseResult розширений для PDF ──────────────────────────────────────────

class PDFParseResult(ParseResult):
    """
    Той самий ParseResult, але з додатковим полем bank_totals —
    точні суми безпосередньо з шапки банківської виписки.
    """
    def __init__(self, rows, skipped, bank, bank_totals=None):
        super().__init__(rows, skipped, bank)
        self.bank_totals: dict = bank_totals or {}


# ─── Відкриття PDF (виправлення BytesIO) ─────────────────────────────────────

def _open_pdf(content: bytes | io.BytesIO):
    """Нормалізує bytes/BytesIO → pdfplumber file object."""
    if isinstance(content, (io.BytesIO, io.RawIOBase)):
        content.seek(0)
        raw = content.read()
    else:
        raw = content
    return pdfplumber.open(io.BytesIO(raw))


# ─── Парсинг шапки A-Bank ─────────────────────────────────────────────────────

_RE_EXPENSES   = re.compile(r"Сума витрат за період[:\s]+([\d\s]+[.,]\d{2})\s*UAH", re.IGNORECASE)
_RE_INCOME     = re.compile(r"Сума зарахувань за період[:\s]+([\d\s]+[.,]\d{2})\s*UAH", re.IGNORECASE)
_RE_BAL_START  = re.compile(r"Баланс на початок періоду[:\s]+([\d\s]+[.,]\d{2})\s*UAH", re.IGNORECASE)
_RE_BAL_END    = re.compile(r"Баланс на кінець періоду[:\s]+([\d\s]+[.,]\d{2})\s*UAH", re.IGNORECASE)
_RE_PERIOD     = re.compile(r"Період[:\s]+(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})")


def _amount_from_match(m) -> Optional[float]:
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_abank_header(pdf) -> dict:
    """
    Витягує підсумки з першої сторінки виписки A-Bank.
    Повертає dict з ключами: expenses, income, balance_start, balance_end, period_from, period_to
    """
    text = pdf.pages[0].extract_text() or ""
    totals: dict = {}

    m = _RE_EXPENSES.search(text)
    if m:
        totals["expenses"] = _amount_from_match(m)

    m = _RE_INCOME.search(text)
    if m:
        totals["income"] = _amount_from_match(m)

    m = _RE_BAL_START.search(text)
    if m:
        totals["balance_start"] = _amount_from_match(m)

    m = _RE_BAL_END.search(text)
    if m:
        totals["balance_end"] = _amount_from_match(m)

    m = _RE_PERIOD.search(text)
    if m:
        totals["period_from"] = m.group(1)
        totals["period_to"] = m.group(2)

    return totals


# ─── Парсер A-Банк: детектор таблиці ─────────────────────────────────────────

def _is_abank_table(table: list[list]) -> bool:
    if not table or len(table) < 2:
        return False
    if len(table[0]) != 11:
        return False
    for row in table[1:4]:
        if len(row) < 5:
            continue
        if (re.match(r"\d{2}\.\d{2}\.\d{4}", str(row[0] or "").strip()) and
                re.match(r"\d{3,4}", str(row[3] or "").strip())):
            return True
    return False


# ─── Парсер A-Банк: рядок таблиці ────────────────────────────────────────────

def _parse_abank_amount(raw: str) -> Optional[float]:
    """'-1 000.00' → -1000.0,  '1 812.00' → 1812.0"""
    if not raw or not raw.strip():
        return None
    cleaned = re.sub(r'(?<=\d) (?=\d)', '', raw.strip())
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_abank_date(raw: str) -> Optional[datetime]:
    """'31.01.2026\n08:48' → datetime"""
    if not raw:
        return None
    normalized = raw.replace("\n", " ").strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _is_garbage(text: str) -> bool:
    """Повертає True якщо рядок складається з нечитабельних символів PDF."""
    if not text:
        return True
    bad = sum(1 for c in text if ord(c) < 32 or ord(c) == 65533 or c in ('•', '□', '■'))
    return bad / max(len(text), 1) > 0.5


def _extract_abank_rows(table: list[list]) -> tuple[list[dict], int]:
    out: list[dict] = []
    skipped = 0
    for row in table[1:]:  # рядок 0 — заголовок
        if len(row) < 5:
            skipped += 1
            continue
        date_raw = str(row[0] or "").strip()
        desc_raw = str(row[2] or "").strip()
        mcc_raw  = str(row[3] or "").strip()
        amt_raw  = str(row[4] or "").strip()

        if not re.match(r"\d{2}\.\d{2}\.\d{4}", date_raw):
            skipped += 1
            continue

        amount = _parse_abank_amount(amt_raw)
        if amount is None:
            skipped += 1
            continue

        date = _parse_abank_date(date_raw) or datetime.now()
        if _is_garbage(desc_raw):
            desc_raw = ""

        out.append({
            "date": date,
            "amount": amount,   # зберігаємо знак: від'ємна = витрата, позитивна = надходження
            "description": desc_raw,
            "mcc": mcc_raw,
        })
    return out, skipped


# ─── Парсер Monobank ─────────────────────────────────────────────────────────

def _is_monobank_table(table: list[list]) -> bool:
    if not table or len(table) < 2:
        return False
    # Monobank pdf has 10 columns per row
    if len(table[0]) != 10:
        return False
    if "Дата" not in str(table[0][0] or ""):
        return False
    return True


def _extract_monobank_rows(table: list[list]) -> tuple[list[dict], int]:
    out: list[dict] = []
    skipped = 0
    for row in table[1:]:
        if len(row) < 4:
            skipped += 1
            continue
            
        date_raw = str(row[0] or "").strip()
        if not date_raw or "Дата" in date_raw:
            skipped += 1
            continue
            
        desc_raw = str(row[1] or "").strip().replace("\n", " ")
        mcc_raw = str(row[2] or "").strip()
        amt_raw = str(row[3] or "").strip()
        
        amount = _parse_abank_amount(amt_raw) # Reusing this as it cleans up spaces and parses float
        if amount is None:
            skipped += 1
            continue
            
        date = _parse_abank_date(date_raw) or datetime.now()
        if _is_garbage(desc_raw):
            desc_raw = "Переказ" if mcc_raw else "Без опису"
            
        final_desc = f"{desc_raw} (MCC {mcc_raw})" if mcc_raw else desc_raw
        
        out.append({
            "date": date,
            "amount": amount,
            "description": final_desc,
            "mcc": mcc_raw,
        })
    return out, skipped


# ─── Класифікація з урахуванням знаку суми ───────────────────────────────────

# MCC-коди, які ЗАВЖДИ є переказами незалежно від знаку
ALWAYS_TRANSFER_MCC = {"6010", "6011", "6012", "6051", "6050"}

# Ключові слова в описі, що означають переказ до накопичень
TRANSFER_OUT_KEYWORDS = [
    "депозит", "вклад", "накопич", "на банку", "заощадж", "скарбничк"
]

# Ключові слова, що однозначно є доходом
INCOME_KEYWORDS = [
    "зарплата", "зп", "salary", "аванс", "виплата", "нарахування зп",
    "заробітна плата", "фріланс", "надходження", "повернення"
]

# Регексп: два+ слова з великої літери кирилицею (повне ім'я людини)
# Приклад: "Андрій Ващук"  або "Маржена Петренко-Савчук"
_RE_PERSON_NAME = re.compile(
    r"^[А-ЯІЇЄҐ][\u0430-\u044f\u0456\u0457\u0454\u0491'\-]{1,20}"
    r"(?:[\s\-][А-ЯІЇЄҐ][\u0430-\u044f\u0456\u0457\u0454\u0491'\-]{1,20}){1,3}$"
)


def _is_person_name(text: str) -> bool:
    """
    Повертає True якщо рядок опису виглядає як ім'я та прізвище людини.
    Ознаки:
      - 2..4 слова
      - кожне з великої літери кирилиці
      - без цифр і без спецсимволів (крім 'і -)
    """
    if not text or len(text) < 5:
        return False
    # Не повинно містити цифри або звичайні спецсимволи
    if re.search(r'[\d@.,/*&#(){}\[\]|]', text):
        return False
    words = text.split()
    if len(words) < 2 or len(words) > 4:
        return False
    return bool(_RE_PERSON_NAME.match(text.strip()))


def _classify_transaction(
    raw_amount: float,
    description: str,
    mcc: str,
) -> tuple[str, str, bool]:
    """
    Визначає (category_name, tx_type, ignore_in_stats) на основі:
      1. Ім'я людини в описі → переказ (найвищий пріоритет)
      2. Знак суми (від'ємна = витрата, позитивна = дохід)
      3. MCC-код
      4. Ключові слова в описі
    """
    desc_lower = description.lower()
    is_positive = raw_amount > 0

    # Пріоритет 1: Ім'я людини → переказ незалежно від знаку чи MCC
    if _is_person_name(description):
        return "Переказ (людині)", "transfer", False

    # Перекази-накопичення (позитивна або від'ємна, але конкретно банківські)
    if mcc in ALWAYS_TRANSFER_MCC:
        if is_positive:
            # Надходження на рахунок — визначаємо чи це дохід чи просто переказ
            if any(kw in desc_lower for kw in INCOME_KEYWORDS):
                return "Інший дохід", "income", False
            # Отримання грошей від когось (МСС 4829 = p2p) → income
            if mcc == "4829":
                return "Інший дохід", "income", False
            # Банківські операції (кредит, ліміт) → transfer
            return "Переказ (інше)", "transfer", False
        else:
            # Відправка грошей → transfer
            if any(kw in desc_lower for kw in TRANSFER_OUT_KEYWORDS):
                return "Інвестиції/Скарбничка", "transfer", False
            return "Переказ (інше)", "transfer", False

    if is_positive:
        # Позитивна сума → явно надходження
        if any(kw in desc_lower for kw in TRANSFER_OUT_KEYWORDS):
            return "Переказ (інше)", "transfer", False
        if any(kw in desc_lower for kw in ["зарплата", "зп", "salary", "аванс"]):
            return "Зарплата", "income", False
        if any(kw in desc_lower for kw in ["фріланс", "freelance", "upwork"]):
            return "Фріланс", "income", False
        cat_name, tx_type, ignore = categorize(description, mcc)
        if tx_type == "expense":
            return "Інший дохід", "income", False
        return cat_name, tx_type, ignore

    else:
        # Від'ємна сума → витрата
        cat_name, tx_type, ignore = categorize(description, mcc)
        if tx_type == "income":
            return "Інше", "expense", False
        return cat_name, tx_type, ignore


# ─── Роутер та Обробка ────────────────────────────────────────────────────────

def _process_raw_rows(raw_rows: list[dict], total_skipped: int, bank: str, bank_totals: dict, user_id: str, categories: list[dict]) -> PDFParseResult:
    out_rows: list[dict] = []
    for parsed in raw_rows:
        raw_amount: float = parsed["amount"]
        abs_amount = abs(raw_amount)
        if abs_amount < 0.01:
            total_skipped += 1
            continue

        cat_name, tx_type, ignore = _classify_transaction(
            raw_amount, parsed["description"], parsed["mcc"]
        )
        category_id = find_category_id(categories, cat_name, tx_type)

        out_rows.append({
            "user_id": user_id,
            "amount": abs_amount,
            "type": tx_type,
            "category_id": category_id,
            "description": (parsed["description"] or "")[:255] or None,
            "source": "csv",  # DB enum: 'manual' | 'csv' (pdf не підтримується)
            "ignore_in_stats": ignore,
            "transaction_date": parsed["date"].isoformat(),
            "metadata": {
                "raw_category": cat_name,
                "mcc": parsed.get("mcc", ""),
                "bank": bank,
                "is_outgoing": raw_amount < 0,
            },
        })

    return PDFParseResult(out_rows, total_skipped, bank, bank_totals)


def _parse_abank_pdf(pdf, user_id: str, categories: list[dict]) -> PDFParseResult:
    raw_rows: list[dict] = []
    total_skipped = 0
    try:
        bank_totals = _extract_abank_header(pdf)
    except Exception:
        bank_totals = {}

    for page in pdf.pages:
        page_tables = page.extract_tables({
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
        })
        if not page_tables:
            page_tables = page.extract_tables({
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "intersection_tolerance": 8,
            }) or []

        for tbl in page_tables:
            if not tbl:
                continue
            if _is_abank_table(tbl):
                rows, skipped = _extract_abank_rows(tbl)
                raw_rows.extend(rows)
                total_skipped += skipped
                
    if not raw_rows:
        return PDFParseResult([], total_skipped, BankFormat.ABANK, bank_totals)
        
    return _process_raw_rows(raw_rows, total_skipped, BankFormat.ABANK, bank_totals, user_id, categories)


def _parse_monobank_pdf(pdf, user_id: str, categories: list[dict]) -> PDFParseResult:
    raw_rows: list[dict] = []
    total_skipped = 0

    for page in pdf.pages:
        page_tables = page.extract_tables({
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
        })
        if not page_tables:
            page_tables = page.extract_tables({
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "intersection_tolerance": 8,
            }) or []

        for tbl in page_tables:
            if not tbl:
                continue
            if _is_monobank_table(tbl):
                rows, skipped = _extract_monobank_rows(tbl)
                raw_rows.extend(rows)
                total_skipped += skipped
                
    if not raw_rows:
        return PDFParseResult([], total_skipped, BankFormat.MONO, {})
        
    return _process_raw_rows(raw_rows, total_skipped, BankFormat.MONO, {}, user_id, categories)


def parse_pdf(
    content: bytes | io.BytesIO,
    user_id: str,
    categories: list[dict],
) -> PDFParseResult:
    """Роутер PDF: визначає банк за першою сторінкою і парсить."""
    with _open_pdf(content) as pdf:
        first_page_text = pdf.pages[0].extract_text() or ""
        text_lower = first_page_text.lower()
        
        if "monobank" in text_lower or "універсал банк" in text_lower or "universal bank" in text_lower:
            return _parse_monobank_pdf(pdf, user_id, categories)
        elif "а-банк" in text_lower or "акцент-банк" in text_lower:
            return _parse_abank_pdf(pdf, user_id, categories)
        else:
            raise ValueError("Невідомий формат виписки. Підтримувані формати: Monobank, А-Банк.")
