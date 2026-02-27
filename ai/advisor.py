"""
Financial Advisor — AI відповіді на фінансові питання юзера.

Використовує llama-3.3-70b-versatile (smart) з повним фінансовим контекстом
у системному промпті. Відповіді короткі, конкретні, без зайвих застережень.

Пам'ять: останні N повідомлень з conversation_memory (Supabase) додаються
до контексту щоб модель «пам'ятала» попередні питання в межах сесії.
"""
from __future__ import annotations
from bot.utils import fmt_amt

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from ai.llm import get_smart_llm
from database import repository as repo

MANDATORY_CATEGORIES = {
    "Супермаркети", "Оренда/Комунальні", "Товари для дому",
    "Зв'язок", "Таксі/Громадський", "Авто", "Ліки/Лікарі"
}

# Кількість повідомлень з history які передаємо в контекст
MEMORY_WINDOW = 8

_TONE_PROMPTS = {
    "casual": (
        "Стиль спілкування: ДРУЖНІЙ/НЕФОРМАЛЬНИЙ.\n"
        "- Спілкуйся як близький друг, який добре розбирається в фінансах.\n"
        "- Використовуй неформальну мову, емодзі, легкий гумор.\n"
        "- Можеш жартувати, але завжди давай корисну пораду.\n"
        "- Звертайся на «ти». Приклад: «Слухай, з твоїм бюджетом це цілком реально! 💪»\n"
    ),
    "balanced": (
        "Стиль спілкування: ЗБАЛАНСОВАНИЙ.\n"
        "- Дружній, але по справі. Без зайвої формальності, але і без жартів.\n"
        "- Звертайся на «ти». Чіткі пояснення з конкретними цифрами.\n"
        "- Приклад: «За твоїми даними ти можеш відкладати 5 000 грн/місяць. Це цілком реалістичний план.»\n"
    ),
    "formal": (
        "Стиль спілкування: ОФІЦІЙНИЙ/ПРОФЕСІЙНИЙ.\n"
        "- Стриманий, ввічливий, діловий тон. Як фінансовий консультант у банку.\n"
        "- Звертайся на «Ви». Чіткі формулювання, структуровані відповіді.\n"
        "- Приклад: «На основі Ваших фінансових показників рекомендую наступну стратегію...»\n"
    ),
}

_ADVISOR_SYSTEM = """Ти — персональний фінансовий аналітик FinanceOS.
Ти ніколи не відмовляєшся відповідати. Твоя ціль — давати конкретні цифри та чіткі рекомендації.

{tone_instructions}

Фінансовий контекст користувача (Згенеровано системою):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Плановий бюджет: {budget_limit} {currency}
Надходження цього місяця: {total_income} {currency}
Загальний дохід (база для розрахунків): {current_limit} {currency}
Всі витрати цього місяця: {total_expenses} {currency}
Обов'язкові витрати: {mandatory_expenses} {currency}
Буфер безпеки (10% від доходу): {safety_buffer} {currency}
Поточний залишок: {remaining} {currency}
Розрахунковий вільний залишок (Дохід - Обов'язкові - Буфер): {free_balance} {currency}

Топ витрат:
{top_categories}

Динаміка та цілі:
{spending_trends}
{goals}

РОЗРАХОВАНІ ВАРІАНТИ НАКОПИЧЕННЯ:
{savings_plans}

ІНФОРМАЦІЯ ПРО ПОВНОТУ ДАНИХ:
{data_sufficiency_warning}
{covered_topics_section}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ПРАВИЛА ТВОЄЇ ВІДПОВІДІ:
1. Ти аналізуєш питання (напр. "чи можу дозволити X за Y грн?").
2. Формат відповіді має бути СТРОГО за такою структурою:

Частина 1 — Поточний стан (1-2 речення): 
Озвуч поточний залишок та вільні кошти. Відповідай, чи може юзер дозволити собі покупку прямо зараз без шкоди для бюджету.

Частина 2 — План накопичення (якщо зараз не може):
Напиши готові розраховані варіанти накопичення з блоку "РОЗРАХОВАНІ ВАРІАНТИ НАКОПИЧЕННЯ" вище.
Назви їх: Комфортний, Помірний, Швидкий.
Якщо вільний залишок від'ємний або нульовий, запропонуй спершу переглянути необов'язкові витрати.

Частина 3 — Рекомендація (1 речення):
Запропонуй варіант, який є найбільш збалансованим для поточної ситуації та поясни чому.

Частина 4 (ЛИШЕ ЯКЩО БЛОК "ІНФОРМАЦІЯ ПРО ПОВНОТУ ДАНИХ" містить попередження):
Додай це попередження у кінці відповіді дослівно або зі збереженням точного сенсу та закликом завантажити виписку командою /upload.

3. НІКОЛИ не вигадуй цифри. Використовуй тільки ті цифри, які надані у блоках вище. Всі розрахунки місяців вже зроблені системою, просто озвуч їх.
4. Якщо питання не стосується разової покупки (напр. загальна порада), адаптуй цю структуру, але обов'язково надай конкретні варіанти заощаджень, що базуються на вільному залишку.
5. Форматування: без markdown зірочок або хешів. Пиши просто і красиво.
"""


def _format_similar_transactions(txs: list[dict]) -> str:
    if not txs:
        return "  Не знайдено релевантних транзакцій"
    lines = []
    for tx in txs:
        # threshold is dynamic, typically > 0.3 for good matches
        if tx.get("similarity", 0) > 0.2:
            lines.append(f"  • {tx.get('content', '')} (схожість: {tx.get('similarity', 0):.2f})")
    return "\n".join(lines) if lines else "  Не знайдено релевантних транзакцій"


def _format_categories(cats: list[dict]) -> str:
    if not cats:
        return "  Немає даних"
    return "\n".join(
        f"  • {c.get('icon', '')} {c.get('name', 'Інше')}: {fmt_amt(c.get('total', 0))} грн"
        for c in cats[:5]
    )


def _format_trends(trends: list[dict]) -> str:
    if not trends:
        return "  Немає історичних даних"
    lines = []
    for t in trends:
        inc = t.get('total_income', 0) or 0
        exp = t.get('total_expenses', 0) or 0
        lines.append(f"  • {t.get('month_period', '')}: Дохід {fmt_amt(inc)}, Витрати {fmt_amt(exp)}".replace(",", " "))
    return "\n".join(lines)


def _format_goals(goals: list[dict]) -> str:
    if not goals:
        return "  Активних цілей немає"
    lines = []
    for g in goals:
        remaining = g.get("target_amount", 0) - g.get("current_amount", 0)
        deposit = g.get("monthly_deposit", 0)
        lines.append(
            f"  • {g.get('name', '?')}: залишилось {fmt_amt(remaining)} грн"
            + (f" (внесок {fmt_amt(deposit)}/міс)" if deposit else "")
        )
    return "\n".join(lines)


async def answer_financial_question(
    question: str,
    user: dict,
    db,
    state=None,
) -> str:
    """
    Основна функція — відповідає на фінансове питання юзера.
    Завантажує контекст з БД, будує промпт, викликає LLM.

    Повертає текст відповіді для відправки у Telegram.
    """
    import calendar
    from datetime import datetime

    import re
    import math

    user_id = user["id"]
    currency = user.get("currency", "₴")
    budget_limit = user.get("monthly_income", 0) or 0
    comm_style = user.get("communication_style", "balanced")

    # ── Завантажуємо фінансовий контекст ─────────────────────────────────────
    balance, all_cats, goals, history, trends, weeks_in_db = await _load_context(db, user_id)

    total_income = balance.get("total_income") or 0
    total_expenses = balance.get("total_expenses") or 0
    current_limit = budget_limit + total_income
    remaining = current_limit - total_expenses

    mandatory_expenses = 0
    has_food_or_transport = False
    for c in all_cats:
        c_name = c.get("name", "")
        c_sum = c.get("total", 0) or 0
        if c_name in MANDATORY_CATEGORIES:
            mandatory_expenses += c_sum
            if c_name in ["Супермаркети", "Таксі/Громадський", "Авто"]:
                has_food_or_transport = True

    safety_buffer = current_limit * 0.10
    free_balance = current_limit - mandatory_expenses - safety_buffer

    # Перевірка достатності даних
    data_sufficiency_warning = "Дані для аналізу достатні."
    is_insufficient = False
    if weeks_in_db < 2:
        is_insufficient = True
    elif not has_food_or_transport and total_expenses == 0:
        is_insufficient = True
        
    if is_insufficient:
        data_sufficiency_warning = (
            "⚠️ Щоб отримати точніший аналіз і реалістичний план накопичення, "
            "рекомендую завантажити виписку за останні 2-3 місяці. "
            "Це дозволить мені побачити твої реальні патерни витрат і дати конкретніші цифри. Команда /upload"
        )

    # Витягуємо суму з питання, якщо є
    cleaned_q = re.sub(r'[\s]', '', question)
    nums = re.findall(r'\b\d+(?:[.,]\d+)?\b', cleaned_q)
    target_amount = max([float(n.replace(',', '.')) for n in nums]) if nums else 0.0

    savings_plans = "Вільний залишок для формування плану складає 0 грн або менше."
    if free_balance > 0:
        comfort_amt = free_balance * 0.15
        moderate_amt = free_balance * 0.30
        fast_amt = free_balance * 0.50

        def calc_months(monthly):
            if target_amount <= 0 or monthly <= 0:
                return ""
            m = math.ceil(target_amount / monthly)
            if m > 120: 
                return " (більше 10 років)"
            return f" (за {m} міс.)"

        savings_plans = (
            f"Комфортний варіант: відкладати 15% вільного залишку — це {fmt_amt(comfort_amt)} грн/міс{calc_months(comfort_amt)}\n"
            f"Помірний варіант: відкладати 30% вільного залишку — це {fmt_amt(moderate_amt)} грн/міс{calc_months(moderate_amt)}\n"
            f"Швидкий варіант: відкладати 50% вільного залишку — це {fmt_amt(fast_amt)} грн/міс{calc_months(fast_amt)}"
        )

    # ── Підбираємо інструкції тональності ─────────────────────────────────────
    tone_instructions = _TONE_PROMPTS.get(comm_style, _TONE_PROMPTS["balanced"])

    # ── Витягуємо previously covered topics ────────────────────────────────
    covered_topics_section = ""
    if state:
        fsm_data = await state.get_data()
        covered = fsm_data.get("covered_topics", [])
        if "накопичення_варіанти" in covered:
            covered_topics_section = "\nВАЖЛИВО: Ти вже розраховував і озвучував варіанти накопичень (Комфортний/Помірний/Швидкий) у цій розмові! Замість повторення розрахунків просто посилайся на попередню відповідь (напр. 'як ми вже порахували вище, відкладай 15%')."

    # ── Будуємо системний промпт з фінансовим знімком ────────────────────────
    system_prompt = _ADVISOR_SYSTEM.format(
        tone_instructions=tone_instructions,
        budget_limit=fmt_amt(budget_limit),
        total_income=fmt_amt(total_income),
        current_limit=fmt_amt(current_limit),
        total_expenses=fmt_amt(total_expenses),
        mandatory_expenses=fmt_amt(mandatory_expenses),
        safety_buffer=fmt_amt(safety_buffer),
        remaining=fmt_amt(remaining),
        free_balance=fmt_amt(free_balance),
        top_categories=_format_categories(all_cats),
        spending_trends=_format_trends(trends),
        goals=_format_goals(goals),
        savings_plans=savings_plans,
        data_sufficiency_warning=data_sufficiency_warning,
        covered_topics_section=covered_topics_section,
        currency=currency,
    )

    # ── Будуємо ланцюжок повідомлень з пам'яттю ──────────────────────────────
    messages = [SystemMessage(content=system_prompt)]

    # Додаємо історію (MEMORY_WINDOW останніх повідомлень)
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "ai":
            messages.append(AIMessage(content=msg["content"]))

    # Додаємо поточне питання
    messages.append(HumanMessage(content=question))

    # ── LLM виклик ───────────────────────────────────────────────────────────
    llm = get_smart_llm()
    response = await llm.ainvoke(messages)
    answer: str = response.content  # type: ignore[assignment]

    # ── Зберігаємо обмін в пам'ять ───────────────────────────────────────────
    # Питання юзера вже збережено в ai_chat.py
    try:
        await repo.save_message(db, user_id, "ai", answer)
    except Exception:
        pass  # Пам'ять не критична — не падаємо якщо не вдалось зберегти

    return answer


async def generate_budget_insight(user: dict, db) -> str:
    """Генерує короткий (1-2 речення) персоналізований інсайт для звіту /budget."""
    user_id = user["id"]
    currency = user.get("currency", "₴")
    budget_limit = user.get("monthly_income", 0) or 0
    comm_style = user.get("communication_style", "balanced")

    balance, all_cats, goals, history, trends, weeks_in_db = await _load_context(db, user_id)
    
    total_income = balance.get("total_income") or 0
    total_expenses = balance.get("total_expenses") or 0
    current_limit = budget_limit + total_income
    remaining = current_limit - total_expenses

    mandatory_expenses = 0
    for c in all_cats:
        c_name = c.get("name", "")
        c_sum = c.get("total", 0) or 0
        if c_name in MANDATORY_CATEGORIES:
            mandatory_expenses += c_sum

    safety_buffer = current_limit * 0.10
    free_balance = current_limit - mandatory_expenses - safety_buffer

    import calendar
    from datetime import datetime
    now = datetime.now()
    _, total_days = calendar.monthrange(now.year, now.month)
    remaining_days = total_days - now.day + 1
    daily_limit = remaining / remaining_days if remaining_days > 0 and remaining > 0 else 0

    tone_instructions = _TONE_PROMPTS.get(comm_style, _TONE_PROMPTS["balanced"])

    system_prompt = _ADVISOR_SYSTEM.format(
        tone_instructions=tone_instructions,
        budget_limit=fmt_amt(budget_limit),
        total_income=fmt_amt(total_income),
        current_limit=fmt_amt(current_limit),
        total_expenses=fmt_amt(total_expenses),
        mandatory_expenses=fmt_amt(mandatory_expenses),
        safety_buffer=fmt_amt(safety_buffer),
        remaining=fmt_amt(remaining),
        free_balance=fmt_amt(free_balance),
        top_categories=_format_categories(all_cats),
        spending_trends=_format_trends(trends),
        goals=_format_goals(goals),
        savings_plans="Не застосовується для загального звіту.",
        data_sufficiency_warning="Дані для аналізу достатні.",
        currency=currency,
    )
    prompt = (
        "Сформуй короткий і чіткий фінансовий інсайт або пораду на основі цих даних (максимум 1 коротке речення, до 15 слів). "
        "Пиши як жива людина (реальний експерт). "
        "ОДРАЗУ переходь до суті. КАТЕГОРИЧНО заборонено використовувати вступні фрази "
        "(типу 'За вашими даними...', 'Аналізуючи стан...', 'Найважливішою порадою буде...'). "
        "Якщо немає витрат — просто підбадьор розпочати їх записувати."
    )
    
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt)
    ]
    
    from ai.llm import get_fast_llm
    llm = get_fast_llm()  # Для інсайту швидкості достатньо
    try:
        response = await llm.ainvoke(messages)
        return response.content
    except Exception as e:
        import loguru
        loguru.logger.error(f"Insight generation failed: {e}")
        return "Всі показники в нормі, продовжуй в тому ж дусі!"


async def _load_context(db, user_id: str) -> tuple:
    """Паралельно завантажуємо всі потрібні дані з Supabase."""
    import asyncio
    balance_task = repo.get_monthly_balance(db, user_id)
    stats_task = repo.get_db_stats(db, user_id)
    goals_task = repo.get_active_goals(db, user_id)
    history_task = repo.get_recent_messages(db, user_id, limit=MEMORY_WINDOW)
    trends_task = repo.get_spending_trends(db, user_id, months=3)

    balance, stats, goals, history, trends = await asyncio.gather(
        balance_task, stats_task, goals_task, history_task, trends_task
    )
    weeks_in_db, all_cats = stats
    return balance, all_cats, goals, history, trends, weeks_in_db
