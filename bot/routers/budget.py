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


def _format_number(n: float) -> str:
    """Форматуємо число: 30500 → '30 500'"""
    return fmt_amt(n)


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
    
    # Ліміт витрат (те що людина вказує при онбордингу як середній дохід)
    budget_limit = user.get("monthly_income", 0) or 0
    comfort_level = user.get("comfort_level", 5)

    total_income = balance.get("total_income") or 0
    total_expenses = balance.get("total_expenses") or 0
    # Фактичний загальний бюджет: базовий + додаткові надходження
    current_budget_limit = budget_limit + total_income
    remaining_budget = current_budget_limit - total_expenses
    
    # ── Дні та ліміти ──────────────────────────────────────────────────────────
    _, total_days = calendar.monthrange(now.year, now.month)
    remaining_days = total_days - now.day + 1
    
    daily_limit = remaining_budget / remaining_days if remaining_days > 0 and remaining_budget > 0 else 0
    
    remaining_pct = (remaining_budget / current_budget_limit * 100) if current_budget_limit > 0 else 0
    comfort_label = get_comfort_label(comfort_level)
    
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
                    
            txns_text += f"• {display_name} ({sign}{_format_number(t['amount'])} {currency})\n"
    else:
        txns_text = "<i>Поки немає записів</i>\n"

    # ── Будівництво звіту ──────────────────────────────────
    bar = _colored_progress_bar(remaining_budget, current_budget_limit, length=10)
    
    report = (
        f"📊 <b>Фінансовий звіт — {month_name}</b>\n"
        f"{comfort_label}\n\n"
        f"🟢 Надходження: {_format_number(total_income)} {currency}\n"
        f"🔴 Витрачено: {_format_number(total_expenses)} {currency}\n\n"
        f"💰 Ваш залишок: {_format_number(remaining_budget)} / {_format_number(current_budget_limit)} {currency}\n"
        f"{bar} ({remaining_pct:.0f}%)\n\n"
        f"⏱ Денний ліміт: {_format_number(daily_limit)} {currency} / день\n\n"
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
