"""
Inline keyboards та CallbackData factory — aiogram 3.x спосіб.

CallbackData factory гарантує типобезпечну роботу з callback_data:
замість магічних рядків типу "confirm_txn:uuid" маємо Pydantic-подібні класи.
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ─── CallbackData Factories ──────────────────────────────────────────────────

class OnboardingAction(CallbackData, prefix="onb"):
    """Дії під час онбордингу."""
    action: str  # "manual" | "csv" | "comfort_N" (де N = 1..5)


class TransactionAction(CallbackData, prefix="txn"):
    """Дії з транзакцією після AI-розпізнавання."""
    action: str      # "confirm" | "reject" | "edit_cat"
    txn_id: str      # UUID транзакції (як рядок)


class CategorySelect(CallbackData, prefix="cat"):
    """Вибір категорії зі списку."""
    category_id: str  # UUID категорії


class GoalAction(CallbackData, prefix="goal"):
    """Дії з ціллю (створення)."""
    action: str      # "confirm" | "cancel" | "no_deadline"
    goal_id: str = ""


class GoalManageAction(CallbackData, prefix="gm"):
    """Дії для редагування/видалення існуючих цілей."""
    action: str      # "list", "select", "edit", "edit_collected", "edit_target", "delete", "confirm_delete", "cancel_delete"
    goal_id: str = ""


# ─── Keyboard Builders ───────────────────────────────────────────────────────

def kb_onboarding_method() -> InlineKeyboardMarkup:
    """Кнопки вибору методу онбордингу."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✍️ Заповнити вручну",
        callback_data=OnboardingAction(action="manual"),
    )
    builder.button(
        text="📂 Завантажити CSV виписку",
        callback_data=OnboardingAction(action="csv"),
    )
    builder.adjust(1)  # Кожна кнопка на окремому рядку
    return builder.as_markup()


def kb_comfort_level() -> InlineKeyboardMarkup:
    """
    Вибір рівня фінансового комфорту (1-5 зірочок).
    Визначає наскільки агресивно бот рекомендуватиме економити.
    """
    builder = InlineKeyboardBuilder()
    comfort_labels = {
        "1": "😌 Живу в задоволення",
        "2": "🙂 Трохи економлю",
        "3": "😐 Баланс між тратами і накопиченням",
        "4": "🧐 Активно заощаджую",
        "5": "💪 Максимальна економія",
    }
    for value, label in comfort_labels.items():
        builder.button(
            text=label,
            callback_data=OnboardingAction(action=f"comfort_{value}"),
        )
    builder.adjust(1)
    return builder.as_markup()


def kb_communication_style() -> InlineKeyboardMarkup:
    """
    Вибір стилю спілкування з ботом.
    Визначає тональність AI-відповідей.
    """
    builder = InlineKeyboardBuilder()
    styles = {
        "casual":   "😎 Дружній — неформальний, з емодзі",
        "balanced": "🙂 Збалансований — дружній, але по справі",
        "formal":   "👔 Офіційний — стриманий і професійний",
    }
    for value, label in styles.items():
        builder.button(
            text=label,
            callback_data=OnboardingAction(action=f"style_{value}"),
        )
    builder.adjust(1)
    return builder.as_markup()


def kb_transaction_confirm(txn_id: str) -> InlineKeyboardMarkup:
    """Кнопки підтвердження/редагування/скасування транзакції."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Підтвердити",
        callback_data=TransactionAction(action="confirm", txn_id=txn_id),
    )
    builder.button(
        text="✏️ Змінити категорію",
        callback_data=TransactionAction(action="edit_cat", txn_id=txn_id),
    )
    builder.button(
        text="❌ Скасувати",
        callback_data=TransactionAction(action="reject", txn_id=txn_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def kb_categories(categories: list[dict], txn_type: str) -> InlineKeyboardMarkup:
    """
    Динамічний список категорій для вибору.
    Показує тільки категорії відповідного типу (income/expense).
    """
    builder = InlineKeyboardBuilder()
    filtered = [c for c in categories if c["type"] == txn_type]
    for cat in filtered:
        builder.button(
            text=f"{cat['icon']} {cat['name']}",
            callback_data=CategorySelect(category_id=cat["id"]),
        )
    builder.adjust(2)  # 2 кнопки на рядок
    return builder.as_markup()


def kb_goal_confirm() -> InlineKeyboardMarkup:
    """Підтвердження або скасування нової цілі."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🎯 Зберегти ціль",
        callback_data=GoalAction(action="confirm"),
    )
    builder.button(
        text="📅 Без дедлайну",
        callback_data=GoalAction(action="no_deadline"),
    )
    builder.button(
        text="❌ Скасувати",
        callback_data=GoalAction(action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def kb_goals_manage_start() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⚙️ Редагувати цілі",
        callback_data=GoalManageAction(action="list")
    )
    return builder.as_markup()


def kb_goals_list(goals: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for g in goals:
        builder.button(
            text=g["name"],
            callback_data=GoalManageAction(action="select", goal_id=str(g["id"]))
        )
    builder.adjust(1)
    return builder.as_markup()


def kb_goal_actions(goal_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✏️ Редагувати",
        callback_data=GoalManageAction(action="edit", goal_id=goal_id)
    )
    builder.button(
        text="🗑 Видалити",
        callback_data=GoalManageAction(action="delete", goal_id=goal_id)
    )
    builder.adjust(2)
    return builder.as_markup()


def kb_goal_edit_options(goal_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💰 Змінити зібране",
        callback_data=GoalManageAction(action="edit_collected", goal_id=goal_id)
    )
    builder.button(
        text="🎯 Змінити цільову суму",
        callback_data=GoalManageAction(action="edit_target", goal_id=goal_id)
    )
    builder.adjust(1)
    return builder.as_markup()


def kb_goal_delete_confirm(goal_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Так, видалити",
        callback_data=GoalManageAction(action="confirm_delete", goal_id=goal_id)
    )
    builder.button(
        text="❌ Ні, скасувати",
        callback_data=GoalManageAction(action="cancel_delete", goal_id=goal_id)
    )
    builder.adjust(2)
    return builder.as_markup()
