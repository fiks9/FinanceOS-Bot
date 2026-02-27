"""
FSM States — aiogram 3.x StatesGroup визначення для всіх сценаріїв бота.

Принцип: кожен сценарій = окремий StatesGroup клас.
FSMContext зберігається в MemoryStorage (MVP) → при рестарті бота стани скидаються.
"""
from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """
    Онбординг нового користувача.
    Флоу: вибір методу → [ручний ввід] → income → comfort → style → done
                       → [CSV] → (передається до CSVStates)
    """
    choosing_method    = State()   # Юзер обирає: вручну чи CSV
    waiting_for_income = State()   # Очікуємо ввід місячного доходу
    waiting_for_comfort = State()  # Обираємо рівень фінансового комфорту (1-10)
    waiting_for_style  = State()   # Обираємо стиль спілкування (casual/balanced/formal)


class AddTransactionStates(StatesGroup):
    """
    Ручне додавання транзакції через FSM (якщо AI не зміг розпарсити).
    Основний шлях — через Intent Detection (без FSM).
    Цей FSM — fallback для уточнення.
    """
    waiting_for_amount   = State()   # Уточнення суми
    waiting_for_category = State()   # Вибір категорії серед доступних
    waiting_for_confirm  = State()   # Підтвердження перед збереженням
    missing_goal_confirm = State()   # Підтвердження створення цілі
    waiting_for_goal_target = State() # Очікуємо детальну інформацію по цілі


class GoalStates(StatesGroup):
    """Постановка фінансової цілі."""
    waiting_for_name     = State()   # Назва цілі ("Планшет", "Відпустка")
    waiting_for_amount   = State()   # Цільова сума
    waiting_for_deadline = State()   # Бажана дата (або "без дати")
    waiting_for_confirm  = State()   # Підтвердження


class ManageGoalStates(StatesGroup):
    """Покрокове керування (редагування або видалення) ціллю."""
    waiting_for_new_collected = State() # Очікуємо ввід нової зібраної суми
    waiting_for_new_target = State()    # Очікуємо ввід нової цільової суми


class CSVStates(StatesGroup):
    """Обробка CSV виписки."""
    waiting_for_file    = State()   # Очікуємо файл (якщо юзер погодився)
    processing          = State()   # Файл отримано, йде обробка
    reviewing_results   = State()   # Юзер переглядає результати категоризації
    waiting_for_confirm = State()   # Зберегти чи скасувати


class EditTransactionStates(StatesGroup):
    """Повний перезапис існуючої транзакції."""
    waiting_for_edit_input = State()  # Очікуємо нове повідомлення (напр. "300 Таксі")
