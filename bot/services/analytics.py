"""
Behavior Analytics — автоматичний аналіз фінансової поведінки юзера.

Оновлює поля профілю:
  - monthly_income_actual   — реальний середній дохід (останні 3 місяці)
  - monthly_expenses_actual — реальні середні витрати (останні 3 місяці)
  - spending_style          — economical / balanced / spender
  - behavior_updated_at     — timestamp перерахунку

Викликається фоново після кожної нової income/expense транзакції.
Мінімальний поріг: не менше 21 дня даних у БД.
"""
from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from database import repository as repo

MIN_DAYS_REQUIRED = 21  # Мінімум 3 тижні даних для висновків


def _classify_spending_style(avg_income: float, avg_expenses: float) -> str | None:
    """Визначає тип фінансової поведінки на основі співвідношення витрат до доходу."""
    if avg_income <= 0:
        return None
    ratio = avg_expenses / avg_income
    if ratio < 0.60:
        return "economical"
    elif ratio <= 0.85:
        return "balanced"
    else:
        return "spender"


async def update_behavior_analytics(db, user_id: str) -> None:
    """
    Перераховує та оновлює аналітику поведінки юзера.
    Викликається фоново — не блокує основний потік відповіді.
    """
    try:
        # Перевіряємо мінімальний поріг даних
        first_date = await repo.get_first_transaction_date(db, user_id)
        if first_date is None:
            return

        # Нормалізуємо timezone
        now = datetime.now(timezone.utc)
        if first_date.tzinfo is None:
            first_date = first_date.replace(tzinfo=timezone.utc)

        days_with_data = (now - first_date).days
        if days_with_data < MIN_DAYS_REQUIRED:
            return

        # Отримуємо середні показники за останні 3 місяці
        averages = await repo.get_monthly_averages(db, user_id, months=3)
        avg_income = averages.get("avg_income", 0.0)
        avg_expenses = averages.get("avg_expenses", 0.0)
        months_with_data = averages.get("months_with_data", 0)

        if months_with_data == 0:
            return

        spending_style = _classify_spending_style(avg_income, avg_expenses)

        update_payload: dict = {
            "monthly_expenses_actual": avg_expenses,
            "behavior_updated_at": now.isoformat(),
        }
        if avg_income > 0:
            update_payload["monthly_income_actual"] = avg_income
        if spending_style:
            update_payload["spending_style"] = spending_style

        await repo.update_user(db, user_id, **update_payload)

        logger.debug(
            f"Behavior analytics updated for user={user_id}: "
            f"income_actual={avg_income}, expenses_actual={avg_expenses}, style={spending_style}"
        )

    except Exception as e:
        logger.error(f"Failed to update behavior analytics for user={user_id}: {e}")
