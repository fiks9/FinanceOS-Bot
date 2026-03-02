"""
Document Handler Router — Smart Import банківських виписок (CSV та PDF).

Підтримувані формати:
  .csv → parse_csv()  — прямий парсинг колонок
  .pdf → parse_pdf()  — pdfplumber-based парсинг таблиць

Флоу:
  1. Юзер кидає .csv або .pdf файл
  2. Бот детектує формат і вибирає парсер
  3. Показує preview (транзакції, розбивка, топ-категорії)
  4. Юзер натискає "Зберегти" або "Скасувати"
  5. Bulk insert в Supabase → звіт
"""
import asyncio
import json
from bot.utils import fmt_amt

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger

from ai.csv_parser import parse_csv, ParseResult, BankFormat
from ai.pdf_parser import parse_pdf
from bot.services.analytics import update_behavior_analytics
from bot.states import CSVStates
from database import repository as repo

router = Router(name="document_handler")

# Ліміт рядків на один імпорт (захист від файлів-монстрів)
MAX_ROWS = 500

# Підтримувані MIME types
CSV_MIME = {"text/csv", "text/plain", "application/octet-stream", "application/csv"}
PDF_MIME = {"application/pdf"}


# ─── Крок 1: Отримання файлу ──────────────────────────────────────────────────

@router.message(F.document)
async def handle_document(message: Message, state: FSMContext, user: dict, db) -> None:
    """Обробляє документ: визначає формат (CSV/PDF) і запускає відповідний парсер."""
    doc = message.document
    file_name = (doc.file_name or "").lower()

    # ── Routing по розширенню ──
    if file_name.endswith(".csv"):
        file_format = "csv"
    elif file_name.endswith(".pdf"):
        file_format = "pdf"
    else:
        await message.answer(
            "⚠️ Непідтримуваний формат файлу.\n\n"
            "Надсилай виписку у форматі:\n"
            "• <b>.csv</b> — текстова виписка (Monobank, ПриватБанк...)\n"
            "• <b>.pdf</b> — PDF-виписка (A-Банк, Ощадбанк...)"
        )
        return

    # ── Завантажуємо файл ──
    status_msg = await message.answer(f"⏳ Аналізую виписку ({file_format.upper()})...")
    try:
        file = await message.bot.get_file(doc.file_id)
        content: bytes = await message.bot.download_file(file.file_path)  # type: ignore
    except Exception as e:
        logger.error(f"Failed to download file: {e}")
        await status_msg.delete()
        await message.answer("⚠️ Не вдалось завантажити файл. Спробуй ще раз.")
        return

    # ── Категорії ──
    try:
        categories = await repo.get_categories_for_user(db, user["id"])
    except Exception as e:
        logger.error(f"Failed to load categories for import: {e}")
        categories = []

    # ── Парсинг ──
    try:
        if file_format == "csv":
            result = parse_csv(content, str(user["id"]), categories)
        else:
            result = parse_pdf(content, str(user["id"]), categories)
    except Exception as e:
        logger.error(f"{file_format.upper()} parsing failed: {e}", exc_info=True)
        await status_msg.delete()
        await message.answer(
            f"⚠️ Не вдалось розпарсити файл.\n\n"
            f"<i>Переконайся, що це оригінальна виписка банку (не відсканована копія).</i>"
        )
        return

    # Видаляємо статус-повідомлення після парсингу
    try:
        await status_msg.delete()
    except Exception:
        pass

    if not result.rows:
        hint = "CSV-виписка" if file_format == "csv" else "PDF-виписка (не скан)"
        await message.answer(
            f"😔 Не вдалось знайти жодної транзакції у файлі.\n"
            f"Пропущено рядків/рядів: {result.skipped}\n\n"
            f"<i>Переконайся, що це {hint} з банку. Скановані PDF не підтримуються.</i>"
        )
        return

    # ── Preview ──
    rows = result.rows[:MAX_ROWS]
    total_found = len(result.rows)
    limited = total_found > MAX_ROWS
    await _show_preview(message, state, rows, result, total_found, limited, file_format)


async def _show_preview(
    message: Message,
    state: FSMContext,
    rows: list[dict],
    result,  # ParseResult або PDFParseResult
    total_found: int,
    limited: bool,
    file_format: str = "csv",
) -> None:
    """Показує summary та ставить FSM-стан перед збереженням."""

    bank_label = BankFormat.LABELS.get(result.bank, "Виписка")
    fmt_icon = "📄" if file_format == "pdf" else "📂"
    fmt_label = "PDF" if file_format == "pdf" else "CSV"
    limit_note = f"\n⚠️ <i>Показано перші {MAX_ROWS} з {total_found} транзакцій.</i>" if limited else ""

    # ── Підсумки: з банківської шапки (якщо є) або рахуємо самі ──
    bank_totals = getattr(result, "bank_totals", {})
    use_bank_totals = bool(bank_totals.get("expenses") or bank_totals.get("income"))

    if use_bank_totals:
        expense_sum = bank_totals.get("expenses") or 0.0
        income_sum  = bank_totals.get("income") or 0.0
        # Перекази — рахуємо зі списку (банк їх включає у витрати/доходи)
        transfer_sum = sum(r["amount"] for r in rows if r["type"] == "transfer")
    else:
        expense_sum = income_sum = transfer_sum = 0.0
        for row in rows:
            if row["type"] == "expense":
                expense_sum += row["amount"]
            elif row["type"] == "income":
                income_sum += row["amount"]
            else:
                transfer_sum += row["amount"]

    # Топ категорії: витрати по категоріях + вихідні перекази
    cat_totals: dict[str, float] = {}
    transfer_total = 0.0
    for row in rows:
        if row["type"] == "expense":
            cat = row["metadata"].get("raw_category", "Інше")
            cat_totals[cat] = cat_totals.get(cat, 0) + row["amount"]
        elif row["type"] == "transfer":
            # Тільки вихідні перекази (гроші що відправили)
            # is_outgoing: True=вихідний, None/відсутній=невідомо(CSV, рахуємо), False=вхідний(не рахуємо)
            is_outgoing = row.get("metadata", {}).get("is_outgoing", None)
            if is_outgoing is not False:
                transfer_total += row["amount"]

    top_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:4]
    lines = [(name, amount) for name, amount in top_cats]
    if transfer_total > 0:
        lines.append(("Перекази", transfer_total))
    # Сортуємо всі рядки (включно з переказами) за сумою
    lines.sort(key=lambda x: x[1], reverse=True)
    top_cats_text = "\n".join(f"  \u2022 {name}: {fmt_amt(amount)} грн" for name, amount in lines) if lines else "  \u2022 Немає витрат"

    # Рядок з балансами (якщо є)
    balance_note = ""
    if bank_totals.get("balance_start") is not None and bank_totals.get("balance_end") is not None:
        period = ""
        if bank_totals.get("period_from") and bank_totals.get("period_to"):
            period = f" ({bank_totals['period_from']} – {bank_totals['period_to']})"
        balance_note = (
            f"\n💳 Баланс: <b>{bank_totals['balance_start']:,.2f}</b> → "
            f"<b>{bank_totals['balance_end']:,.2f} грн</b>{period}"
        )

    text = (
        f"{fmt_icon} <b>Smart Import [{fmt_label}] — {bank_label}</b>\n\n"
        f"Знайдено транзакцій: <b>{len(rows)}</b>{limit_note}\n"
        f"Пропущено рядків: {result.skipped}\n"
        f"{balance_note}\n\n"
        f"💸 Витрати: <b>{fmt_amt(expense_sum)} грн</b>\n"
        f"💰 Доходи: <b>{fmt_amt(income_sum)} грн</b>\n\n"
        f"<b>Топ категорії витрат:</b>\n{top_cats_text}\n\n"
        f"Зберегти всі транзакції в базу?"
    )

    await state.set_state(CSVStates.waiting_for_confirm)
    await state.update_data(pending_csv=json.dumps(rows, default=str))

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Зберегти все", callback_data="csv_confirm_yes"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="csv_confirm_no"),
    ]])
    await message.answer(text, reply_markup=keyboard)


# ─── Крок 2: Підтвердження / Скасування ──────────────────────────────────────

@router.callback_query(
    CSVStates.waiting_for_confirm,
    F.data.in_({"csv_confirm_yes", "csv_confirm_no"})
)
async def handle_csv_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user: dict,
    db,
) -> None:
    """Зберігає транзакції або скасовує імпорт."""
    # Видаляємо повідомлення з кнопками
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)

    if callback.data == "csv_confirm_no":
        await state.clear()
        await callback.message.answer("❌ Імпорт скасовано. Дані не збережено.")
        await callback.answer()
        return

    data = await state.get_data()
    await state.clear()

    raw = data.get("pending_csv")
    if not raw:
        await callback.message.answer("⚠️ Сесію імпорту втрачено. Надішли файл ще раз.")
        await callback.answer()
        return

    rows: list[dict] = json.loads(raw)

    save_msg = await callback.bot.send_message(callback.from_user.id, "💾 Зберігаю транзакції...")

    try:
        saved = await repo.bulk_insert_transactions(db, rows)
        count = len(saved)
    except Exception as e:
        logger.error(f"Bulk insert failed for user {user['id']}: {e}")
        try:
            await save_msg.delete()
        except Exception:
            pass
        await callback.bot.send_message(
            callback.from_user.id,
            "⚠️ Сталась помилка при збереженні. Спробуй ще раз або зверніться до підтримки."
        )
        await callback.answer()
        return

    # Видаляємо статус-повідомлення
    try:
        await save_msg.delete()
    except Exception:
        pass

    # Автоматично позначаємо юзера як онбордингованого
    # (важливо після /clear, щоб /budget працював без повторного /start)
    if not user.get("onboarded"):
        try:
            await repo.update_user(db, user["id"], onboarded=True)
        except Exception:
            pass

    # Фінальний звіт
    expense_count = sum(1 for r in rows if r["type"] == "expense")
    income_count = sum(1 for r in rows if r["type"] == "income")
    transfer_count = sum(1 for r in rows if r["type"] == "transfer")

    await callback.bot.send_message(
        callback.from_user.id,
        f"✅ <b>Імпорт завершено!</b>\n\n"
        f"Збережено: <b>{count}</b> транзакцій\n"
        f"  ➖ Витрати: {expense_count}\n"
        f"  ➕ Доходи: {income_count}\n"
        f"  ↔️ Перекази: {transfer_count}\n\n"
        f"Переглянь свій оновлений звіт: /budget"
    )
    logger.info(f"Import: saved {count} transactions for user {user['id']}")

    # Фоновий перерахунок аналітики поведінки після масового імпорту
    asyncio.create_task(update_behavior_analytics(db, str(user["id"])))

    await callback.answer()

