"""
Budget Router — відображення гібридного фінансового звіту (Дашборд).
Плановий бюджет поєднується з фактичним кешфлоу.
"""
import calendar
from bot.utils import fmt_amt
from datetime import datetime
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from database import repository as repo
from ai.digest import generate_weekly_digest

router = Router(name="budget")


def get_comfort_label(level: int) -> str:
    if level >= 9:
        return "<b>🛡 Режим:</b> Сувора економія (Максимальне заощадження)"
    elif level >= 7:
        return "<b>🚀 Ціль:</b> Агресивне накопичення"
    elif level >= 5:
        return "<b>⚖️ Стратегія:</b> Розумний баланс (Стабільний дохід)"
    elif level >= 3:
        return "<b>🏄‍♂️ Стиль:</b> Свобода витрат (Помірне заощадження)"
    else:
        return "<b>👑 Статус:</b> Комфорт (Без жорстких лімітів)"


def _colored_progress_bar(remaining: float, total: float, length: int = 10) -> str:
    """Батарея залишку: заповнюється на відсоток залишку бази+доходу.
    Багато - зелена, половина - жовта, мало - червона."""
    if total <= 0:
        return "⬜️" * length
    
    pct = max(min(remaining / total, 1.0), 0.0)
    filled_count = round(pct * length)
    
    if pct >= 0.5:
        color = "🟩"
    elif pct >= 0.2:
        color = "🟨"
    else:
        color = "🟥"
        
    return (color * filled_count) + ("⬜️" * (length - filled_count))


@router.message(Command("budget"))
async def cmd_budget(message: Message, user: dict, db) -> None:
    """Показує фінансовий звіт за поточний місяць."""
    user_id = user["id"]

    try:
        balance, recent_txns, insight = await _fetch_snapshot_data(db, user)
    except Exception as e:
        logger.exception(f"Failed to fetch budget data for user {user_id}: {e}")
        await message.answer("⚠️ Не вдалось завантажити дані. Спробуй пізніше.")
        return

    report = _build_budget_report(
        user=user,
        balance=balance,
        recent_txns=recent_txns,
        insight=insight,
    )

    await message.answer(report, parse_mode="HTML")


async def _fetch_snapshot_data(db, user: dict) -> tuple:
    """Завантажуємо баланс, останні транзакції та генеруємо AI інсайт."""
    import asyncio
    from ai.advisor import generate_budget_insight

    user_id = user["id"]
    balance_task = repo.get_monthly_balance(db, user_id)
    txns_task = repo.get_recent_transactions(db, user_id, limit=3)
    insight_task = generate_budget_insight(user, db)

    balance, txns, insight = await asyncio.gather(balance_task, txns_task, insight_task)
    return balance, txns, insight


def _build_budget_report(user: dict, balance: dict, recent_txns: list, insight: str) -> str:
    now = datetime.now()
    UKR_MONTHS = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                  "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
    month_name = f"{UKR_MONTHS[now.month - 1]} {now.year}"
    currency = user.get("currency", "₴")
    comfort_level = user.get("comfort_level", 5)

    # Очікуваний дохід — довідкова цифра з профілю (не впливає на баланс)
    income_expected = user.get("monthly_income", 0) or 0
    # Реальний середній дохід з аналітики (якщо вже є достатньо даних)
    income_actual_avg = user.get("monthly_income_actual") or 0

    # Баланс виключно на основі реальних транзакцій
    total_income = balance.get("total_income") or 0
    total_expenses = balance.get("total_expenses") or 0
    remaining_budget = total_income - total_expenses

    # ── Дні та ліміти ──────────────────────────────────────────────────────────
    _, total_days = calendar.monthrange(now.year, now.month)
    remaining_days = total_days - now.day + 1

    # Для денного ліміту: якщо дохід цього місяця ще не зафіксований —
    # беремо очікуваний як орієнтир для підрахунку
    income_ref = total_income if total_income > 0 else income_expected
    remaining_ref = income_ref - total_expenses
    daily_limit = remaining_ref / remaining_days if remaining_days > 0 and remaining_ref > 0 else 0

    remaining_pct = (remaining_budget / total_income * 100) if total_income > 0 else 0
    comfort_label = get_comfort_label(comfort_level)

    # ── Рядок порівняння (очікуване vs реальне) ──────────────────────────────
    comparison_line = ""
    if income_actual_avg > 0 and abs(income_actual_avg - income_expected) > income_expected * 0.05:
        diff = income_actual_avg - income_expected
        sign = "+" if diff > 0 else ""
        comparison_line = (
            f"📈 Реальний середній дохід: {fmt_amt(income_actual_avg)} {currency} "
            f"({sign}{fmt_amt(diff)})\n"
        )
    elif total_income == 0 and income_expected > 0:
        comparison_line = f"⏳ Очікуваний дохід: {fmt_amt(income_expected)} {currency} (ще не зафіксовано)\n"

    expenses_actual_avg = user.get("monthly_expenses_actual") or 0
    if expenses_actual_avg > 0 and total_expenses > expenses_actual_avg * 1.1:
        diff = total_expenses - expenses_actual_avg
        comparison_line += f"⚠️ Витрати на {fmt_amt(diff)} вище середнього\n"

    # ── Останні операції ──────────────────────────────────────────────────
    txns_text = ""
    if recent_txns:
        for t in recent_txns:
            cat = t.get("categories") or {}
            cat_name = cat.get("name", "Інше")
            if t["type"] == "income":
                sign = "+"
                display_name = cat_name
            else:
                sign = "-"
                desc = t.get("description")
                display_name = desc if desc else cat_name
                if not display_name:
                    display_name = "Витрата"
            txns_text += f"• {display_name} ({sign}{fmt_amt(t['amount'])} {currency})\n"
    else:
        txns_text = "<i>Поки немає записів</i>\n"

    # ── Будівництво звіту ──────────────────────────────────────────────────
    bar = _colored_progress_bar(remaining_budget, total_income, length=10)

    report = (
        f"📊 <b>Фінансовий звіт — {month_name}</b>\n"
        f"{comfort_label}\n\n"
        f"🟢 Надходження: {fmt_amt(total_income)} {currency}\n"
        f"🔴 Витрачено: {fmt_amt(total_expenses)} {currency}\n\n"
        f"💰 Ваш залишок: {fmt_amt(remaining_budget)} {currency}\n"
        f"{bar} ({remaining_pct:.0f}%)\n"
        f"{comparison_line}\n"
        f"⏱ Денний ліміт: {fmt_amt(daily_limit)} {currency} / день\n\n"
        f"📉 <b>Останні записи:</b>\n"
        f"{txns_text}\n"
        f"💡 <b>AI-Аналіз:</b> {insight}"
    )

    return report


@router.message(Command("digest"))
async def cmd_digest(message: Message, user: dict, db) -> None:
    """Для дебагу — примусово генерує і відправляє Weekly Digest."""
    await message.answer("🔄 Генерую тижневий звіт. Секундочку...")
    try:
        digest_text = await generate_weekly_digest(user, db)
        if digest_text:
            await message.answer(digest_text, parse_mode="HTML")
        else:
            await message.answer("⚠️ Не вдалось згенерувати звіт або немає даних.")
    except Exception as e:
        logger.exception(f"Failed to generate digest for {user['id']}: {e}")
        await message.answer("⚠️ Сталася помилка при генерації звіту.")
