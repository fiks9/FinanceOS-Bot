"""
AI Chat Router — головний catchall обробник вільного тексту.

ОБОВ'ЯЗКОВО підключається ОСТАННІМ в Dispatcher (вже налаштовано в setup.py).

Флоу обробки повідомлення:
1. Перевіряємо чи юзер онбордований
2. Показуємо typing... (chat_action)
3. Detect Intent — classify текст (smart LLM)
4. Розгалуження:
   - ADD_TRANSACTION → extract → перевірка балансу → [підтвердження] → save → fast LLM confirmation
   - FIN_QUESTION    → Financial context + smart LLM answer
   - SET_GOAL        → Заглушка (Крок 5)
   - UNKNOWN         → коротка відповідь що не зрозуміло
"""
import json
from bot.utils import fmt_amt

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger

from ai.intent import detect_intent, extract_transaction, extract_goal, extract_goal_management, generate_confirmation
from ai.advisor import answer_financial_question
from bot.states import AddTransactionStates
from models.schemas import IntentType
from database import repository as repo

router = Router(name="ai_chat")

# Поріг впевненості — якщо нижче, питаємо юзера що він мав на увазі
CONFIDENCE_THRESHOLD = 0.6

# Якщо витрата перевищує залишок більше ніж на X% — запитати підтвердження
OVERSPEND_WARN_RATIO = 0.9   # попереджаємо якщо витрата > 90% залишку
OVERSPEND_BLOCK_RATIO = 1.0  # блокуємо (питаємо підтвердження) якщо > 100%


# ─── Обробка станів цілей (FSM) ───────────────────────────────────────────────
import re
from bot.states import GoalStates

@router.message(GoalStates.waiting_for_amount, F.text)
async def process_goal_amount(message: Message, state: FSMContext, user: dict, db) -> None:
    text = message.text.strip()
    from bot.parsers import parse_natural_amount
    amount = parse_natural_amount(text)
    
    if amount is None or amount <= 0:
        await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
        return
        
    await state.update_data(goal_amount=amount)
    await state.set_state(GoalStates.waiting_for_deadline)
    await message.answer(
        "За який термін хочеш накопичити? Наприклад за 3, 6 або 12 місяців\n"
        "<i>(або напиши текст «без терміну»)</i>",
        parse_mode="HTML"
    )

@router.message(GoalStates.waiting_for_deadline, F.text)
async def process_goal_deadline(message: Message, state: FSMContext, user: dict, db) -> None:
    text = message.text.strip()
    nums = re.findall(r'\b\d+\b', text)
    months = int(nums[0]) if nums else None
    
    data = await state.get_data()
    goal_name = data.get("goal_name", "Нова ціль")
    goal_amount = data.get("goal_amount", 0)
    
    await state.clear()
    
    # Формуємо рядок з усіма даними і викликаємо обробку
    if months:
        combined_text = f"{goal_name} Сума цілі: {goal_amount} грн. Термін: {months} місяців."
    else:
        combined_text = f"{goal_name} Сума цілі: {goal_amount} грн. Без визначеного терміну."
        
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    await _try_extract_and_save_goal(message, combined_text, user, db, state, skip_deadline_prompt=True)


# ─── Основний обробник ────────────────────────────────────────────────────────

async def _build_history_context(user_id, db, state: FSMContext) -> str:
    history_lines = []
    try:
        recent_msgs = await repo.get_recent_messages(db, user_id, limit=8)
        for m in recent_msgs:
            role = "Юзер" if m["role"] == "user" else ("Бот" if m["role"] == "ai" else "Система")
            history_lines.append(f"{role}: {m['content']}")
    except Exception as e:
        logger.error(f"Failed to load history context: {e}")
        
    context_str = ""
    if history_lines:
        context_str += "Останні 8 повідомлень розмови:\n" + "\n".join(history_lines) + "\n\n"
        
    data = await state.get_data()
    last_action = data.get("last_action")
    covered_topics = data.get("covered_topics", [])
    
    if last_action:
        context_str += f"Остання дія бота (last_action): {last_action}\n"
    if covered_topics:
        context_str += f"Вже обговорені теми (covered_topics): {', '.join(covered_topics)}\n"
        
    return context_str

@router.message(F.text)
async def handle_free_text(message: Message, user: dict, db, state: FSMContext) -> None:
    """Основний обробник будь-якого текстового повідомлення."""

    if not user.get("onboarded"):
        await message.answer("👋 Давай спочатку налаштуємо твій профіль — /start")
        return

    text = message.text.strip()
    user_id = user["id"]

    # Показуємо індикатор друку — бот "думає"
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    # Зберігаємо питання юзера в пам'ять ОДРАЗУ
    # (а відповіді збережуться в кінці відповідних функцій)
    try:
        await repo.save_message(db, user_id, "user", text)
    except Exception as e:
        logger.error(f"Failed to save user message to memory: {e}")

    # Полегшуємо роботу LLM: якщо текст містить суму (25к, двадцять тисяч),
    # ми явно додаємо її в кінець повідомлення перед детекцією та екстракцією.
    from bot.parsers import parse_natural_amount
    parsed_amt = parse_natural_amount(text)
    if parsed_amt is not None:
        text = f"{text} (Сума: {parsed_amt})"

    history_context = await _build_history_context(user_id, db, state)

    # ── Крок 1: Intent Detection ──────────────────────────────────────────────
    try:
        intent_result = await detect_intent(text, history_context)
    except Exception as e:
        logger.error(f"Intent detection failed for user {user_id}: {e}")
        await message.answer("🤔 Щось пішло не так при обробці. Спробуй ще раз.")
        return

    logger.info(
        f"Intent: {intent_result.intent} (confidence={intent_result.confidence:.2f}) "
        f"for user={user_id}, text={text!r}"
    )

    # ── Крок 2: Розгалуження по intent ───────────────────────────────────────
    if intent_result.intent == IntentType.EDIT_LAST_ACTION:
        await _handle_edit_last_action(message, text, user, db, state, intent_result)

    elif intent_result.intent == IntentType.ADD_TRANSACTION:
        await _handle_add_transaction(message, text, user, db, state)

    elif intent_result.intent == IntentType.FIN_QUESTION:
        await _handle_fin_question(message, text, user, db, state)

    elif intent_result.intent == IntentType.SET_GOAL:
        await _handle_set_goal(message, text, user, db, state, intent_result)

    elif intent_result.intent == IntentType.MANAGE_GOAL:
        await _handle_manage_goal(message, text, user, db)

    elif intent_result.intent == IntentType.GENERAL_CHAT:
        await _handle_general_chat(message, text, user, db)

    else:  # UNKNOWN — все одно пробуємо дати корисну відповідь
        await _handle_general_chat(message, text, user, db)


# ─── ADD_TRANSACTION flow ─────────────────────────────────────────────────────

async def _handle_add_transaction(
    message: Message,
    text: str,
    user: dict,
    db,
    state: FSMContext,
) -> None:
    """
    Повний цикл додавання транзакції:
    extract → перевірка балансу → [підтвердження якщо overspend] → save → підтвердити.
    """
    user_id = user["id"]

    # ── 1. Завантажуємо категорії юзера ──────────────────────────────────────
    try:
        categories = await repo.get_categories_for_user(db, user_id)
    except Exception as e:
        logger.error(f"Failed to load categories for {user_id}: {e}")
        categories = []

    # ── 2. Extract транзакції ─────────────────────────────────────────────────
    try:
        txn = await extract_transaction(text, categories)
    except Exception as e:
        logger.error(f"Transaction extraction failed for user {user_id}: {e}")
        await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
        return

    logger.info(
        f"Extracted: {txn.type} '{txn.category}' {txn.amount} "
        f"(conf={txn.confidence:.2f}) ignore={txn.ignore_in_stats} user={user_id}"
    )

    if txn.amount <= 0:
        await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
        return

    # Якщо LLM не впевнений — просимо уточнити
    if txn.confidence < CONFIDENCE_THRESHOLD:
        await message.answer(
            f"🤔 Схоже на транзакцію, але я не впевнений.\n\n"
            f"Ти мав на увазі: <b>{fmt_amt(txn.amount)} грн</b> на <b>{txn.category}</b>?\n\n"
            f"Напиши чіткіше, наприклад:\n"
            f"<code>витратив {fmt_amt(txn.amount)} на {txn.category.lower()}</code>"
        )
        return

    # ── 3. Знаходимо category_id (нечутливий до регістру + fuzzy fallback) ───
    category_id = _find_category_id(categories, txn.category, txn.type)

    # ── 4. Перевірка балансу перед збереженням (тільки для expense) ───────────
    if txn.type == "expense" and not txn.ignore_in_stats:
        balance = await repo.get_monthly_balance(db, user_id)
        budget_limit = (user.get("monthly_income") or 0) + (balance.get("total_income") or 0)
        spent = balance.get("total_expenses") or 0
        remaining = budget_limit - spent

        if txn.amount > remaining:
            # Зберігаємо pending транзакцію в FSM state
            await state.set_state(AddTransactionStates.waiting_for_confirm)
            await state.update_data(
                pending_txn=json.dumps({
                    "amount": txn.amount,
                    "type": txn.type,
                    "category": txn.category,
                    "description": txn.description,
                    "ignore_in_stats": txn.ignore_in_stats,
                    "category_id": str(category_id) if category_id else None,
                })
            )

            if remaining <= 0:
                warn_text = (
                    f"⚠️ <b>Бюджет вичерпано!</b>\n\n"
                    f"Твій поточний залишок: <b>0 грн</b> або менше.\n"
                    f"Ця витрата ({fmt_amt(txn.amount)} грн) поглибить мінус.\n\n"
                    f"Все одно зберегти?"
                )
            else:
                warn_text = (
                    f"⚠️ <b>Велика витрата!</b>\n\n"
                    f"Залишок бюджету: <b>{fmt_amt(remaining)} грн</b>\n"
                    f"Ця витрата: <b>{fmt_amt(txn.amount)} грн</b>\n"
                    f"Після збереження: <b>{fmt_amt(remaining - txn.amount)} грн</b>\n\n"
                    f"Все вірно?"
                )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Так, зберегти", callback_data="txn_confirm_yes"),
                InlineKeyboardButton(text="❌ Скасувати", callback_data="txn_confirm_no"),
            ]])
            await message.answer(warn_text, reply_markup=keyboard)
            return

    # ── 5. Якщо переказ на ціль, перевіряємо чи вона існує ─────────────────────────────
    if txn.type == "transfer" and hasattr(txn, "goal_name") and txn.goal_name:
        active_goals = await repo.get_active_goals(db, user_id)
        goal_id = _find_goal_id(active_goals, txn.goal_name)
        
        if not goal_id:
            # Ціль не знайдена — пропонуємо створити
            await state.set_state(AddTransactionStates.missing_goal_confirm)
            await state.update_data(
                pending_txn=json.dumps({
                    "amount": txn.amount,
                    "type": txn.type,
                    "category": txn.category,
                    "description": txn.description,
                    "ignore_in_stats": txn.ignore_in_stats,
                    "category_id": str(category_id) if category_id else None,
                    "goal_name": txn.goal_name,
                })
            )

            warn_text = (
                f"У тебе немає цілі <b>{txn.goal_name}</b>.\n"
                f"Бажаєш створити її зараз?"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Так, створити", callback_data="goal_create_yes"),
                InlineKeyboardButton(text="❌ Ні, просто переказ", callback_data="goal_create_no"),
            ]])
            await message.answer(warn_text, reply_markup=keyboard)
            return

    # ── 6. Зберігаємо транзакцію ──────────────────────────────────────────────
    await _save_and_confirm(
        message=message,
        user_id=str(user_id),
        category_id=category_id,
        txn=txn,
        db=db,
    )


@router.callback_query(
    AddTransactionStates.waiting_for_confirm,
    F.data.in_({"txn_confirm_yes", "txn_confirm_no"})
)
async def handle_txn_confirm(
    callback: CallbackQuery,
    user: dict,
    db,
    state: FSMContext,
) -> None:
    """Обробляє підтвердження або скасування великої витрати."""
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)

    if callback.data == "txn_confirm_no":
        await state.clear()
        await callback.message.answer("❌ Транзакцію скасовано.")
        await callback.answer()
        return

    # Читаємо pending транзакцію з FSM state
    data = await state.get_data()
    await state.clear()

    raw = data.get("pending_txn")
    if not raw:
        await callback.message.answer("⚠️ Не вдалось відновити транзакцію. Спробуй ще раз.")
        await callback.answer()
        return

    txn_data = json.loads(raw)
    from models.schemas import TransactionExtract

    # Відновлюємо об'єкт для generate_confirmation
    txn = TransactionExtract(
        amount=txn_data["amount"],
        type=txn_data["type"],
        category=txn_data["category"],
        description=txn_data.get("description"),
        ignore_in_stats=txn_data.get("ignore_in_stats", False),
        confidence=1.0,
    )

    await _save_and_confirm(
        message=callback.message,
        user_id=str(user["id"]),
        category_id=txn_data.get("category_id"),
        txn=txn,
        db=db,
    )
    await callback.answer()


@router.callback_query(
    AddTransactionStates.missing_goal_confirm,
    F.data.in_({"goal_create_yes", "goal_create_no"})
)
async def handle_goal_create_confirm(
    callback: CallbackQuery,
    user: dict,
    db,
    state: FSMContext,
) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass

    data = await state.get_data()
    raw = data.get("pending_txn")
    if not raw:
        await callback.message.answer("⚠️ Транзакцію втрачено. Спробуй ще раз.")
        await callback.answer()
        return

    txn_data = json.loads(raw)
    goal_name = txn_data.get("goal_name")

    if callback.data == "goal_create_no":
        # Скасовуємо створення цілі і просто зберігаємо як переказ
        txn_data["goal_name"] = None # Знімаємо прив'язку
        from models.schemas import TransactionExtract
        txn = TransactionExtract(**txn_data)
        txn.confidence = 1.0
        
        await state.clear()
        await _save_and_confirm(
            message=callback.message, user_id=str(user["id"]),
            category_id=txn_data.get("category_id"), txn=txn, db=db
        )
        await callback.answer()
        return

    # Якщо Так — питаємо ціль
    await state.set_state(AddTransactionStates.waiting_for_goal_target)
    await callback.message.answer(
        f"🎯 Створюємо ціль <b>{goal_name}</b>.\n\n"
        f"Напиши загальну суму та термін (наприклад, <code>10000 на 6 місяців</code>), "
        f"або просто перерви командою /cancel."
    )
    await callback.answer()


@router.message(AddTransactionStates.waiting_for_goal_target, F.text)
async def handle_goal_target_input(message: Message, user: dict, db, state: FSMContext) -> None:
    text = message.text.strip()
    user_id = user["id"]
    
    data = await state.get_data()
    raw = data.get("pending_txn")
    if not raw:
        await state.clear()
        return
        
    txn_data = json.loads(raw)
    goal_name = txn_data.get("goal_name")
    
    from ai.intent import extract_goal
    try:
        goal = await extract_goal(f"Ціль {goal_name}: {text}")
    except Exception as e:
        await message.answer("⚠️ Не вдалось розпізнати суму цілі. Напиши ще раз:")
        return
        
    from datetime import datetime
    import calendar
    from dateutil.relativedelta import relativedelta
    
    deadline_str = None
    monthly_deposit = None
    if goal.deadline_months and goal.deadline_months > 0:
        now = datetime.now()
        target_date = now + relativedelta(months=int(goal.deadline_months))
        last_day = calendar.monthrange(target_date.year, target_date.month)[1]
        deadline_date = target_date.replace(day=last_day).date()
        deadline_str = deadline_date.isoformat()
        monthly_deposit = goal.target_amount / goal.deadline_months
        
    try:
        await repo.add_goal(
            db=db, user_id=user_id, name=goal.name if goal.name else goal_name,
            target_amount=goal.target_amount, deadline=deadline_str, monthly_deposit=monthly_deposit
        )
    except Exception as e:
        await message.answer("⚠️ Збій під час створення цілі.")
        await state.clear()
        return
        
    await message.answer(f"✅ Ціль <b>{goal_name}</b> створено (на суму {fmt_amt(goal.target_amount)} грн).")

    from models.schemas import TransactionExtract
    txn = TransactionExtract(**txn_data)
    txn.confidence = 1.0
    await state.clear()
    
    await _save_and_confirm(
        message=message, user_id=str(user_id), category_id=txn_data.get("category_id"),
        txn=txn, db=db
    )


# ─── Допоміжні функції ────────────────────────────────────────────────────────

async def _save_and_confirm(message, user_id: str, category_id, txn, db) -> None:
    """Зберігає транзакцію в БД та надсилає підтвердження."""
    try:
        await repo.add_transaction(
            db=db,
            user_id=user_id,
            category_id=category_id,
            amount=txn.amount,
            type=txn.type,
            description=txn.description,
            ignore_in_stats=txn.ignore_in_stats,
            source="manual",
        )
    except Exception as e:
        logger.error(f"Failed to save transaction for {user_id}: {e}")
        await message.answer("⚠️ Не вдалось зберегти. Спробуй ще раз.")
        return

    # Якщо це переказ на ціль — додаємо amount до цілі
    goal_msg = ""
    if txn.type == "transfer" and hasattr(txn, "goal_name") and txn.goal_name:
        try:
            active_goals = await repo.get_active_goals(db, user_id)
            goal_id = _find_goal_id(active_goals, txn.goal_name)
            if goal_id:
                updated_goal = await repo.update_goal_progress(db, goal_id, txn.amount)
                if updated_goal:
                    current = updated_goal.get("current_amount", 0)
                    target = updated_goal.get("target_amount", 0)
                    goal_msg = f"\n🎯 Відкладено на <b>{txn.goal_name}</b>. Зібрано: {fmt_amt(current)} з {fmt_amt(target)} грн."
            else:
                goal_msg = f"\n⚠️ Ціль <b>{txn.goal_name}</b> не знайдено, гроші просто записані як переказ."
        except Exception as e:
            logger.error(f"Failed to update goal for {user_id}: {e}")

    try:
        confirmation = await generate_confirmation(txn, "")
        if goal_msg:
            confirmation += goal_msg
    except Exception as e:
        logger.warning(f"Confirmation LLM failed, using template: {e}")
        sign = "↔️" if txn.type == "transfer" else ("➖" if txn.type == "expense" else "➕")
        desc = txn.description or txn.category
        confirmation = f"{sign} Зберіг: {fmt_amt(txn.amount)} грн — {desc}{goal_msg}"

    await message.answer(confirmation)
    
    # Зберігаємо контекст розмови
    try:
        # Для _save_and_confirm text ми не знаємо точно оригінального тексту (може бути state reload),
        # але тут важливо зберегти хоча б відповідь асистента
        await repo.save_message(db, user_id, "ai", confirmation)
    except Exception as e:
        logger.error(f"Failed to save conversation memory: {e}")


def _find_goal_id(goals: list[dict], goal_name: str) -> str | None:
    """Точний або частковий пошук цілі за назвою."""
    name_q = goal_name.lower().strip()
    
    for g in goals:
        if g.get("name", "").lower().strip() == name_q:
            return g.get("id")
            
    for g in goals:
        db_name = g.get("name", "").lower().strip()
        if name_q in db_name or db_name in name_q:
            return g.get("id")
            
    return None


def _find_category_id(categories: list[dict], category_name: str, txn_type: str) -> str | None:
    """
    Шукає category_id за назвою та типом.
    Алгоритм (від точного до нечіткого):
      1. Точний збіг (case-insensitive)
      2. Одна назва є частиною іншої (наприклад LLM повертає 'Кава', в БД 'Кава/Снеки')
      3. Fallback: категорія з найбільшим збігом токенів
    """
    name_q = category_name.lower().strip()
    same_type = [c for c in categories if c.get("type") == txn_type]

    # 1. Точний збіг
    for cat in same_type:
        if cat.get("name", "").lower().strip() == name_q:
            return cat["id"]

    # 2. Substring match в обидва боки (LLM "кава" vs DB "Кава/Снеки")
    for cat in same_type:
        db_name = cat.get("name", "").lower().strip()
        if name_q in db_name or db_name in name_q:
            return cat["id"]

    # 3. Token overlap — хоча б одне слово збігається
    q_tokens = set(name_q.replace("/", " ").split())
    best_id, best_score = None, 0
    for cat in same_type:
        db_tokens = set(cat.get("name", "").lower().replace("/", " ").split())
        score = len(q_tokens & db_tokens)
        if score > best_score:
            best_score = score
            best_id = cat["id"]

    if best_score > 0:
        return best_id

    return None


async def _handle_fin_question(message: Message, text: str, user: dict, db, state: FSMContext) -> None:
    """Відповідає на фінансове питання юзера через Financial Advisor."""
    try:
        answer = await answer_financial_question(text, user, db, state)
        
        # Перевіряємо, чи були розраховані варіанти накопичень
        if "Комфортний" in answer or "Помірний" in answer or "Швидкий" in answer:
            fsm_data = await state.get_data()
            covered_topics = fsm_data.get("covered_topics", [])
            if "накопичення_варіанти" not in covered_topics:
                covered_topics.append("накопичення_варіанти")
                await state.update_data(covered_topics=covered_topics)
                
        await message.answer(answer)
        
        # Зберігаємо відповідь
        try:
            await repo.save_message(db, user["id"], "ai", answer)
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"Financial advisor failed for user {user['id']}: {e}")
        await message.answer(
            "⚠️ Не вдалось отримати відповідь. Спробуй /budget для перегляду свого стану."
        )


async def _handle_general_chat(message: Message, text: str, user: dict, db) -> None:
    """
    Відповідає на привітання, smalltalk, загальні питання про бота.
    Використовує LLM з адаптивним стилем спілкування.
    """
    from ai.llm import get_fast_llm
    from ai.advisor import _TONE_PROMPTS
    from database import repository as repo

    user_id = user["id"]
    comm_style = user.get("communication_style", "balanced")
    tone = _TONE_PROMPTS.get(comm_style, _TONE_PROMPTS["balanced"])

    system_prompt = (
        "Ти — персональний фінансовий асистент FinanceOS у Telegram.\n"
        "Відповідаєш виключно українською мовою.\n\n"
        f"{tone}\n"
        "Твої можливості:\n"
        "- Вести облік витрат та доходів у розмовному форматі\n"
        "- Відповідати на фінансові питання та давати поради\n"
        "- Ставити і відстежувати фінансові цілі накопичення\n"
        "- Аналізувати банківські виписки (PDF від Monobank, A-Bank)\n"
        "- Показувати фінансовий звіт за місяць\n\n"
        "Правила:\n"
        "- На привітання — привітайся у відповідь і коротко запропонуй чим можеш допомогти.\n"
        "- На питання 'що ти вмієш' — перелічи 3-4 ключові можливості.\n"
        "- На подяку — подякуй у відповідь.\n"
        "- Якщо це нефінансове питання — м'яко поверни розмову до фінансів.\n"
        "- Будь лаконічним: 2-4 речення максимум.\n"
        "- Форматування: без markdown зірочок або хешів.\n"
    )

    from langchain_core.messages import HumanMessage, SystemMessage
    llm = get_fast_llm()

    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=text),
        ])
        answer = response.content
        await message.answer(answer)

        # Зберігаємо відповідь
        try:
            await repo.save_message(db, user_id, "ai", answer)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"General chat failed for user {user_id}: {e}")
        await message.answer(
            "👋 Привіт! Я — FinanceOS, твій фінансовий помічник.\n\n"
            "Напиши мені щось типу:\n"
            "• <code>витратив 200 на таксі</code>\n"
            "• <code>скільки я витратив цього місяця?</code>\n"
            "• <code>хочу накопичити на ноутбук</code>"
        )


async def _handle_set_goal(message: Message, text: str, user: dict, db, state: FSMContext, intent_result) -> None:
    """Обробка intent-у постановки цілі. Перевіряє наявність суми перед викликом LLM."""
    import re
    from bot.states import GoalStates
    
    # Використовуємо знайдену назву цілі (якщо LLM зміг витягнути), інакше сирий текст
    goal_name = intent_result.goal_name if intent_result and intent_result.goal_name else text
    
    # Крок 1 — перевірка на наявність чисел (якщо суми немає, одразу йдемо в FSM)
    has_numbers = bool(re.search(r'\d+', text))
    if not has_numbers:
        await state.update_data(goal_name=goal_name)
        await state.set_state(GoalStates.waiting_for_amount)
        await message.answer(f"Чудово! Скільки приблизно коштуватиме ця ціль ({goal_name})?")
        return

    # Якщо числа є, пробуємо витягнути дані через LLM
    # Передаємо goal_name разом з текстом для кращого парсингу
    enhanced_text = f"Ціль: {goal_name}. {text}"
    await _try_extract_and_save_goal(message, enhanced_text, user, db, state)


async def _try_extract_and_save_goal(message: Message, text: str, user: dict, db, state: FSMContext, skip_deadline_prompt: bool = False) -> None:
    """Виклик GoalExtract з перехопленням можливої помилки відсутності суми та збереження цілі."""
    from datetime import datetime
    import calendar
    from dateutil.relativedelta import relativedelta
    from bot.states import GoalStates
    
    user_id = user["id"]
    try:
        goal = await extract_goal(text)
    except Exception as e:
        err_str = str(e)
        logger.error(f"Goal extraction failed for user {user_id}: {e}")
        # Перехоплюємо 400 помилку (скоріш за все через target_amount <= 0)
        if "400" in err_str and "tool_use_failed" in err_str:
            await state.update_data(goal_name=text)
            await state.set_state(GoalStates.waiting_for_amount)
            await message.answer("Скільки приблизно коштуватиме ця ціль?")
            return
            
        await message.answer("⚠️ Не зміг розпізнати ціль. Спробуй: <code>хочу накопичити 20000 на ноутбук за 6 місяців</code>")
        return
        
    # Крок 5: Додатковий захист, якщо раптом сума нульова (або її не вдалося зчитати)
    if goal.target_amount is None or goal.target_amount <= 0:
        await state.update_data(goal_name=text)
        await state.set_state(GoalStates.waiting_for_amount)
        await message.answer("Скільки приблизно коштуватиме ця ціль?")
        return
        
    if goal.confidence < CONFIDENCE_THRESHOLD:
        await message.answer(
            f"🤔 Звучить як ціль, але я не впевнений.\n"
            f"Ти хочеш накопичити <b>{fmt_amt(goal.target_amount)} грн</b> на <b>{goal.name}</b>?\n"
            f"Напиши трохи чіткіше."
        )
        return
        
    # Розрахунок дедлайну та щомісячного внеску
    deadline_date = None
    deadline_str = None
    monthly_deposit = None
    
    if goal.deadline_months and goal.deadline_months > 0:
        now = datetime.now()
        target_date = now + relativedelta(months=int(goal.deadline_months))
        # Ставимо останній день місяця як дедлайн
        last_day = calendar.monthrange(target_date.year, target_date.month)[1]
        deadline_date = target_date.replace(day=last_day).date()
        deadline_str = deadline_date.isoformat()
        
        monthly_deposit = goal.target_amount / goal.deadline_months
    else:
        # Якщо немає терміну, і ми не прийшли вже з FSM, запитаємо термін (Крок 3)
        if not skip_deadline_prompt:
            await state.update_data(goal_name=goal.name, goal_amount=goal.target_amount)
            await state.set_state(GoalStates.waiting_for_deadline)
            await message.answer(
                "За який термін хочеш накопичити? Наприклад за 3, 6 або 12 місяців\n"
                "<i>(або напиши текст «без терміну»)</i>", 
                parse_mode="HTML"
            )
            return
        
    try:
        new_goal = await repo.add_goal(
            db=db,
            user_id=user_id,
            name=goal.name,
            target_amount=goal.target_amount,
            deadline=deadline_str,
            monthly_deposit=monthly_deposit
        )
        
        # Зберігаємо останню дію (last_action) для можливої корекції
        await state.update_data(last_action={
            "type": "goal_created",
            "goal_id": new_goal.get("id"),
            "name": goal.name,
            "amount": goal.target_amount
        })
    except Exception as e:
        logger.error(f"Failed to save goal for user {user_id}: {e}")
        await message.answer("⚠️ Сталась помилка при збереженні цілі.")
        return
        
    reply = f"🎯 <b>Ціль збережено!</b>\n\nМоя ціль: <b>{goal.name}</b>\nСума: <b>{fmt_amt(goal.target_amount)} грн</b>\n"
    if goal.deadline_months and goal.deadline_months > 0:
        reply += f"Термін: <b>через {goal.deadline_months} міс.</b>\nРекомендовано відкладати: <b>{fmt_amt(monthly_deposit)} грн/місяць</b>\n"
    reply += "\nПереглянути всі цілі: /goals"
    
    await message.answer(reply)
    
    # Зберігаємо відповідь
    try:
        await repo.save_message(db, user_id, "ai", reply)
    except Exception:
        pass


async def _handle_manage_goal(message: Message, text: str, user: dict, db) -> None:
    """Витягує параметри редагування/видалення цілі та виконує їх."""
    user_id = user["id"]
    try:
        manage_data = await extract_goal_management(text)
    except Exception as e:
        logger.error(f"Goal manage extraction failed for user {user_id}: {e}")
        await message.answer("⚠️ Не вдалось розпізнати дію. Спробуй: <code>змінити суму цілі Відпустка на 30000</code>")
        return
        
    if manage_data.confidence < CONFIDENCE_THRESHOLD:
        await message.answer(
            f"🤔 Я не дуже впевнений.\nТи хочеш "
            f"{'видалити ' if manage_data.action == 'delete' else 'змінити '}ціль <b>{manage_data.goal_name}</b>?\n"
            f"Напиши трохи чіткіше."
        )
        return
        
    # Знаходимо ціль
    active_goals = await repo.get_active_goals(db, user_id)
    goal_id = _find_goal_id(active_goals, manage_data.goal_name)
    
    if not goal_id:
        await message.answer(f"⚠️ У тебе немає активної цілі на ім'я <b>{manage_data.goal_name}</b>.")
        return
        
    goal = next((g for g in active_goals if g["id"] == goal_id), None)
    
    if manage_data.action == "delete":
        await repo.delete_goal(db, goal_id, user_id)
        reply = f"✅ Ціль <b>{goal['name']}</b> успішно видалено."
        
    elif manage_data.action == "update_collected":
        if manage_data.new_amount is None:
            await message.answer("⚠️ Я не зрозумів нову суму. Спробуй ще раз.")
            return
            
        await repo.update_goal(db, goal_id, user_id, current_amount=manage_data.new_amount)
        reply = f"✅ Для цілі <b>{goal['name']}</b> встановлено зібрану суму: <b>{fmt_amt(manage_data.new_amount)} грн</b>."
        
    elif manage_data.action == "update_target":
        if manage_data.new_amount is None:
            await message.answer("⚠️ Я не зрозумів нову цільову суму. Спробуй ще раз.")
            return
            
        await repo.update_goal(db, goal_id, user_id, target_amount=manage_data.new_amount)
        reply = f"✅ Для цілі <b>{goal['name']}</b> встановлено нову цільову суму: <b>{fmt_amt(manage_data.new_amount)} грн</b>."

    else:
        reply = "⚠️ Невідома дія."

    await message.answer(reply)
    
    # Зберігаємо відповідь
    try:
        await repo.save_message(db, user_id, "ai", reply)
    except Exception:
        pass


async def _handle_edit_last_action(message: Message, text: str, user: dict, db, state: FSMContext, intent_result) -> None:
    """Обробляє редагування останньої дії бота."""
    user_id = user["id"]
    fsm_data = await state.get_data()
    last_action = fsm_data.get("last_action")
    
    if not last_action:
        reply = "⚠️ Я не можу пригадати, яку саме дію потрібно виправити."
        await message.answer(reply)
        return
        
    if last_action.get("type") == "goal_created":
        goal_id = last_action.get("goal_id")
        
        # Визначаємо що ми міняємо
        new_name = intent_result.goal_name or last_action.get("name")
        new_amount = intent_result.goal_amount or last_action.get("amount")
        
        try:
            await repo.update_goal(db, goal_id, user_id, name=new_name, target_amount=new_amount)
            new_action = {
                "type": "goal_created",
                "goal_id": goal_id,
                "name": new_name,
                "amount": new_amount
            }
            await state.update_data(last_action=new_action)
            reply = f"✅ Виправив ціль. Тепер це: <b>{new_name}</b> ({fmt_amt(new_amount)} грн)"
        except Exception as e:
            logger.error(f"Error editing goal: {e}")
            reply = "⚠️ Помилка при зміні."
            
        await message.answer(reply)
    else:
        reply = "⚠️ Наразі я можу виправляти тільки створення цілей."
        await message.answer(reply)
        
    try:
        await repo.save_message(db, user_id, "ai", reply)
    except Exception:
        pass

