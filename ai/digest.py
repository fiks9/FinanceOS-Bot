import asyncio
from bot.utils import fmt_amt
from loguru import logger

from ai.llm import get_fast_llm
from ai.advisor import _load_context, _TONE_PROMPTS, _format_categories, _format_trends, _format_goals
from database import repository as repo

_DIGEST_SYSTEM = """Ти — персональний фінансовий аналітик FinanceOS.
ТВОЯ ЗАДАЧА: Скласти ЖИВИЙ та ЦІКАВИЙ проактивний тижневий звіт (Weekly Digest).

{tone_instructions}

Твоя ціль — вказати на конкретні досягнення чи прорахунки без нудних шаблонів. Пиши так, ніби ти пишеш повідомлення в Telegram другові-клієнту.
ОБОВ'ЯЗКОВО ЗВЕРНИ УВАГУ НА:
- Як поточні витрати співвідносяться з плановим бюджетом.
- Прогрес цілей (скільки залишилось).
- Динаміку в порівнянні з минулими місяцями (якщо є).

ОБМЕЖЕННЯ:
- Максимум 3-4 речення.
- Мінімум "води". Одразу до суті.
- Жодних привітань ("Привіт, аналізую твій звіт..."), одразу починай з потужного інсайту.

Поточний фінансовий знімок:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Плановий бюджет: {budget_limit} {currency}
Фактичні надходження: {total_income} {currency}
Всього витрачено: {total_expenses} {currency}

Топ категорії:
{top_categories}

Історія (може бути порожньою):
{spending_trends}

Цілі:
{goals}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

async def generate_weekly_digest(user: dict, db) -> str:
    user_id = user["id"]
    currency = user.get("currency", "₴")
    budget_limit = user.get("monthly_income", 0) or 0
    comm_style = user.get("communication_style", "balanced")
    
    # ── Завантажуємо базовий контекст ──────────────────────────────────────────
    balance, top_cats, goals, _, trends, _ = await _load_context(db, user_id)
    
    total_income = balance.get("total_income") or 0
    total_expenses = balance.get("total_expenses") or 0
    
    # Якщо не було ніякої активності, краще повернути дефолт або пропустити
    if total_income == 0 and total_expenses == 0 and not goals:
        return "👋 Привіт! Не бачу активності у твоєму бюджеті останнім часом. Запиши перші витрати, і наступного тижня я зроблю цікавий аналіз!"

    tone_instructions = _TONE_PROMPTS.get(comm_style, _TONE_PROMPTS["balanced"])
    
    system_prompt = _DIGEST_SYSTEM.format(
        tone_instructions=tone_instructions,
        budget_limit=fmt_amt(budget_limit),
        total_income=fmt_amt(total_income),
        total_expenses=fmt_amt(total_expenses),
        top_categories=_format_categories(top_cats),
        spending_trends=_format_trends(trends),
        goals=_format_goals(goals),
        currency=currency,
    )
    
    prompt = "Напиши тижневий дайджест для мене (пряме звернення)."

    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt)
    ]
    
    llm = get_fast_llm()
    try:
        response = await llm.ainvoke(messages)
        return f"🌟 <b>Твій Weekly Digest</b>\n\n{response.content}"
    except Exception as e:
        logger.error(f"Weekly digest skipped for {user_id}: {e}")
        return ""
