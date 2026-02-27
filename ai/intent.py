"""
Intent Detection Pipeline — серце AI-логіки бота.

Два кроки:
1. CLASSIFY — визначаємо intent (ADD_TRANSACTION / FIN_QUESTION / SET_GOAL / UNKNOWN)
   Модель: llama-3.3-70b-versatile (smart) — structured output через with_structured_output()

2. EXTRACT (якщо intent == ADD_TRANSACTION) — витягуємо транзакцію з тексту
   Модель: llama-3.3-70b-versatile (smart) — structured output → TransactionExtract

Обидва кроки використовують LangChain structured output (Pydantic v2),
що гарантує типобезпечний результат без ручного парсингу JSON.
"""
from __future__ import annotations
from bot.utils import fmt_amt

from langchain_core.messages import HumanMessage, SystemMessage

from ai.llm import get_smart_llm
from models.schemas import IntentSchema, IntentType, TransactionExtract, GoalExtract, GoalManageExtract


# ─── Системні промпти ─────────────────────────────────────────────────────────

_INTENT_SYSTEM = """Ти — аналізатор повідомлень для фінансового Telegram-бота.
Твоя задача — визначити тип повідомлення користувача.

Типи намірів (від найвищого пріоритету до найнижчого):
- EDIT_LAST_ACTION: користувач хоче змінити або виправити останню дію бота (наприклад, виправити назву збереженої цілі). ВАЖЛИВО: заповни поля goal_name, goal_amount, goal_months якщо вони вказані в уточненні.
  Приклади: "ні, телефон", "я мав на увазі інше", "виправ на відпустку", "не 12000 а 15000"
- ADD_TRANSACTION: користувач хоче зафіксувати витрату, дохід або переказ.
  Приклади: "витратив 200 на таксі", "купив каву 60 грн", "зп прийшла 30000", "заплатив за комуналку 1500", "відклав 500 на планшет"
- SET_GOAL: користувач хоче поставити нову фінансову ціль. ВАЖЛИВО: Обов'язково заповни поля goal_name. Якщо вказана сума, заповни goal_amount. Якщо кількість місяців, то goal_months. Якщо чогось немає, залиш null.
  Приклади: "хочу накопичити 20000 на ноутбук", "постав ціль — планшет за 12000", "хочу накопичити на подорож" (тут amount і months = null)
- MANAGE_GOAL: користувач хоче змінити або видалити існуючу ціль.
  Приклади: "видали ціль планшет", "змінити суму цілі відпустка на 30000", "оновити зібране для цілі MacBook — 5000"
- FIN_QUESTION: фінансове питання, прохання про пораду, аналіз витрат чи планування.
  Приклади: "чи можу я дозволити відпустку", "скільки я витратив цього місяця", "як накопичити на машину", "чи варто відкладати гроші", "порадь як заощадити", "проведи аналіз моїх фінансів", "мій заробіток 25к, витрати 20к, чи можу дозволити подорож?"
- GENERAL_CHAT: привітання, завершення розмови, загальні питання про бота, smalltalk, та будь-яке дружнє спілкування.
  Приклади: "привіт", "як справи", "що ти вмієш", "допоможи мені", "дякую", "бувай", "хто ти?", "розкажи про себе"
- UNKNOWN: повністю нерозпізнане повідомлення, спам, випадковий текст без сенсу.

Відповідай ТІЛЬКИ у вказаному JSON форматі. Не додавай пояснень поза JSON.
{history_context}
"""

_EXTRACT_SYSTEM = """Ти — парсер фінансових транзакцій.
Витягни з повідомлення суму, тип (income/expense/transfer) та категорію.

Доступні категорії витрат (expense):
{expense_categories}

Доступні категорії доходів (income):
{income_categories}

Доступні категорії переказів/обмінів (transfer):
{transfer_categories}

Правила:
- amount: завжди позитивне число (без знаку), ВКЛЮЧАЮЧИ ДЕСЯТКОВІ якщо вони є (напр. 100.50). НЕ заокруглюй!
- type: "expense" (витрати/комуналка/розваги), "income" (зп/подарунки), "transfer" (переказ на свою банку, відкладання, обмін валют)
- category: ТОЧНА назва з доступного списку вище.
- description: стисло що це (1-5 слів).
- goal_name: якщо це переказ/відкладання грошей на ціль (наприклад "відклав на планшет"), то вкажи назву цілі ("планшет"). Якщо ні — null.
- ignore_in_stats: встановлюй TRUE, якщо це ПОВЕРНЕННЯ БОРГУ, поділ чеку з друзями, або витрата/дохід, який НЕ повинен міняти реальну статистику життя.
- confidence: наскільки впевнений (0.0-1.0)

Відповідай ТІЛЬКИ у вказаному JSON форматі."""


_GOAL_SYSTEM = """Ти — аналізатор фінансових цілей.
Витягни з повідомлення назву цілі (name), суму (target_amount) та термін в місяцях (deadline_months), якщо є.
Правила:
- target_amount: завжди позитивне число
- name: 1-3 слова (наприклад 'Планшет', 'Відпустка на морі')
- deadline_months: число місяців, або null якщо не вказано
- confidence: впевненість (0.0-1.0)
Відповідай ТІЛЬКИ у вказаному JSON форматі."""


_GOAL_MANAGE_SYSTEM = """Ти — парсер дій з фінансовими цілями.
Витягни з повідомлення назву цілі (goal_name), дію (action) та нову суму (new_amount), якщо є.
Правила:
- action: "update_collected" (зміна вже зібраної суми), "update_target" (зміна загальної цілі), або "delete" (повне видалення цілі).
- goal_name: назва цілі, яку треба змінити або видалити (наприклад 'Планшет', 'Відпустка').
- new_amount: нове значення суми. Якщо action="delete", то null.
- confidence: впевненість (0.0-1.0)
Відповідай ТІЛЬКИ у вказаному JSON форматі."""


# ─── Pipeline функції ─────────────────────────────────────────────────────────

async def detect_intent(text: str, history_context: str = "") -> IntentSchema:
    """
    Крок 1: Визначаємо тип повідомлення.
    Повертає IntentSchema з полем intent та confidence.
    """
    llm = get_smart_llm()
    structured_llm = llm.with_structured_output(IntentSchema)
    
    formatted_system = _INTENT_SYSTEM.format(history_context=history_context)

    messages = [
        SystemMessage(content=formatted_system),
        HumanMessage(content=text),
    ]

    result: IntentSchema = await structured_llm.ainvoke(messages)
    return result


async def extract_transaction(
    text: str,
    categories: list[dict],
) -> TransactionExtract:
    """
    Крок 2: Витягуємо деталі транзакції з тексту.
    Викликається тільки якщо intent == ADD_TRANSACTION.

    categories: список dict з полями {name, type, icon} з БД юзера.
    """
    llm = get_smart_llm()
    structured_llm = llm.with_structured_output(TransactionExtract)

    # Розбиваємо категорії по типах для промпту
    expense_cats = ", ".join(
        f"{c.get('icon', '')} {c['name']}" for c in categories if c.get("type") == "expense"
    )
    income_cats = ", ".join(
        f"{c.get('icon', '')} {c['name']}" for c in categories if c.get("type") == "income"
    )
    transfer_cats = ", ".join(
        f"{c.get('icon', '')} {c['name']}" for c in categories if c.get("type") == "transfer"
    )

    system_prompt = _EXTRACT_SYSTEM.format(
        expense_categories=expense_cats or "Інше",
        income_categories=income_cats or "Зарплата",
        transfer_categories=transfer_cats or "Переказ (інше)",
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=text),
    ]

    result: TransactionExtract = await structured_llm.ainvoke(messages)
    return result


async def extract_goal(text: str) -> GoalExtract:
    """
    Витягуємо деталі фінансової цілі з тексту.
    Викликається тільки якщо intent == SET_GOAL.
    """
    llm = get_smart_llm()
    structured_llm = llm.with_structured_output(GoalExtract)

    messages = [
        SystemMessage(content=_GOAL_SYSTEM),
        HumanMessage(content=text),
    ]

    result: GoalExtract = await structured_llm.ainvoke(messages)
    return result


async def extract_goal_management(text: str) -> GoalManageExtract:
    """
    Витягуємо деталі керування ціллю (редагування/видалення).
    Викликається тільки якщо intent == MANAGE_GOAL.
    """
    llm = get_smart_llm()
    structured_llm = llm.with_structured_output(GoalManageExtract)

    messages = [
        SystemMessage(content=_GOAL_MANAGE_SYSTEM),
        HumanMessage(content=text),
    ]

    result: GoalManageExtract = await structured_llm.ainvoke(messages)
    return result


async def generate_confirmation(
    txn: TransactionExtract,
    user_name: str,
) -> str:
    """
    Генерує природне підтвердження збереження транзакції.
    Використовує FAST модель — швидко і дешево по токенах.
    """
    from ai.llm import get_fast_llm

    llm = get_fast_llm()

    sign = "↔️" if txn.type == "transfer" else ("➖" if txn.type == "expense" else "➕")
    desc = txn.description or txn.category

    prompt = (
        f"Підтверди збереження транзакції одним коротким і чітким реченням. "
        f"Приклад: 'Транзакцію підтверджую. Внесено витрату 150 грн на {desc}'.\n"
        f"Транзакція: {sign} {fmt_amt(txn.amount)} грн — {desc}.\n"
        f"НЕ згадуй категорію взагалі. Без привітань та зайвих слів."
    )

    response = await llm.ainvoke([HumanMessage(content=prompt)])
    return response.content  # type: ignore[return-value]
