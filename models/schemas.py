"""
Pydantic v2 моделі для валідації структурованих виводів LLM.
Реалізація: Крок 3 (Intent Detection + Transaction Extraction).
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    ADD_TRANSACTION = "ADD_TRANSACTION"
    FIN_QUESTION = "FIN_QUESTION"
    SET_GOAL = "SET_GOAL"
    MANAGE_GOAL = "MANAGE_GOAL"
    UPDATE_PROFILE = "UPDATE_PROFILE"
    GENERAL_CHAT = "GENERAL_CHAT"
    EDIT_LAST_ACTION = "EDIT_LAST_ACTION"
    UNKNOWN = "UNKNOWN"


class IntentSchema(BaseModel):
    """Результат Intent Detection — визначення типу запиту юзера."""
    intent: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: Optional[str] = None  # Для дебагу (не показується юзеру)
    goal_name: Optional[str] = Field(None, description="Назва цілі (для SET_GOAL та EDIT_LAST_ACTION)")
    goal_amount: Optional[float] = Field(None, description="Сума (для SET_GOAL та EDIT_LAST_ACTION)")
    goal_months: Optional[int] = Field(None, description="Термін в місяцях (для SET_GOAL та EDIT_LAST_ACTION)")


class TransactionExtract(BaseModel):
    """Витягнута транзакція з вільного тексту юзера."""
    amount: float = Field(ge=0, description="Позитивне число. Якщо юзер взагалі не вказав суму, поверни 0.")
    type: Literal["income", "expense", "transfer"]
    category: str = Field(description="Назва категорії зі списку доступних категорій")
    description: Optional[str] = Field(None, description="Короткий опис транзакції")
    goal_name: Optional[str] = Field(None, description="Назва фінансової цілі, якщо це відкладання на конкретну ціль (вже існуючу або нову). Інакше null.")
    ignore_in_stats: bool = Field(False, description="TRUE якщо транзакція НЕ має враховуватись (повернення боргу, спільний чек, інвестиція).")
    confidence: float = Field(ge=0.0, le=1.0)


class GoalExtract(BaseModel):
    """Витягнута фінансова ціль з тексту."""
    name: str = Field(description="Назва цілі, наприклад 'Планшет' чи 'Відпустка', 1-3 слова.")
    target_amount: float = Field(gt=0, description="Сума, яку потрібно накопичити.")
    deadline_months: Optional[int] = Field(None, description="Через скільки місяців користувач хоче досягнути цілі (якщо вказано).")
    confidence: float = Field(ge=0.0, le=1.0)


class GoalManageExtract(BaseModel):
    """Витягнута дія з керування ціллю (редагування/видалення) з тексту."""
    action: Literal["update_collected", "update_target", "delete"] = Field(description="Тип дії (зміна зібраного, зміна цілі або видалення цілі)")
    goal_name: str = Field(description="Назва існуючої цілі, з якою проводимо дію")
    new_amount: Optional[float] = Field(None, gt=0, description="Нове значення суми (тільки для update_collected або update_target)")
    confidence: float = Field(ge=0.0, le=1.0)


class ProfileUpdateExtract(BaseModel):
    """Витягнуте оновлення профілю з тексту."""
    new_income: Optional[float] = Field(None, gt=0, description="Новий місячний дохід, якщо вказано.")
    confidence: float = Field(ge=0.0, le=1.0)


class FinancialSnapshot(BaseModel):
    """Фінансовий знімок юзера — підставляється в System Prompt перед кожним LLM запитом."""
    user_id: str
    monthly_income: float
    total_income_this_month: float
    total_expenses_this_month: float
    net_balance: float
    free_balance: float  # net_balance мінус обов'язкові статті витрат
    top_categories: list[dict]  # [{"name": "Їжа", "total": 3500, "icon": "🛒"}]
    active_goals: list[dict]    # [{"name": "Планшет", "remaining": 8000, "monthly_deposit": 2000}]
    currency: str = "UAH"
