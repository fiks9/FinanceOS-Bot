"""
Onboarding Router — повна реалізація сценарію першого знайомства з ботом.

Флоу:
1. /start → перевіряємо чи юзер вже онбордований
   → якщо так: показуємо головне меню
   → якщо ні:  пропонуємо обрати метод (вручну або CSV)

2. Ручний онбординг:
   → питаємо місячний дохід
   → питаємо рівень комфорту
   → зберігаємо в БД → повідомляємо що готово

3. CSV онбординг:
   → інформуємо що треба надіслати файл
   → FSM передає управління до CSVStates (рhандлер в document_handler.py)
"""
from aiogram import F, Router
from bot.utils import fmt_amt
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.keyboards import (
    OnboardingAction,
    kb_comfort_level,
    kb_communication_style,
    kb_onboarding_method,
)
from bot.states import OnboardingStates, CSVStates
from database import repository as repo

router = Router(name="onboarding")


# ─── /clear (Видалення акаунту) ───────────────────────────────────────────────

@router.message(Command("clear"))
async def cmd_clear(message: Message, state: FSMContext, user: dict, db) -> None:
    """Запитує підтвердження перед повним видаленням даних."""
    await state.clear()
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Так, стерти все!", callback_data="clear_confirm_yes")],
        [InlineKeyboardButton(text="❌ Ні, скасувати", callback_data="clear_confirm_no")],
    ])
    
    await message.answer(
        "⚠️ <b>УВАГА! ВИЛУЧЕННЯ ДАНИХ</b> ⚠️\n\n"
        "Ви дійсно хочете повністю видалити свій профіль?\n\n"
        "<b>Це знищить НАЗАВЖДИ:</b>\n"
        "• Всі ваші транзакції та звіти\n"
        "• Всі налаштовані категорії та фінансові цілі\n"
        "• Історію розмов з AI-помічником\n\n"
        "Цю дію неможливо скасувати.",
        reply_markup=keyboard
    )


@router.callback_query(F.data.in_({"clear_confirm_yes", "clear_confirm_no"}))
async def handle_clear_confirmation(callback: CallbackQuery, user: dict, db) -> None:
    """Обробляє відповідь юзера на підтвердження видалення."""
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)
    
    if callback.data == "clear_confirm_no":
        await callback.message.answer("✅ Видалення скасовано. Ваші дані у безпеці.")
        await callback.answer()
        return

    # Якщо юзер підтвердив видалення:
    try:
        await repo.delete_user(db, user["id"])
        await callback.message.answer(
            "🗑 <b>Всі ваші дані були успішно стерті.</b>\n\n"
            "Ваш профіль, фінансові цілі, графік витрат, пам'ять розмов "
            "та всі транзакції видалено назавжди.\n\n"
            "Якщо захочете користуватись ботом знову — натисніть /start"
        )
        logger.info(f"User {user['id']} (TG: {callback.from_user.id}) wiped their data via /clear.")
    except Exception as e:
        logger.error(f"Failed to wipe user {user['id']}: {e}")
        await callback.message.answer("⚠️ Сталась помилка під час видалення вашого профілю.")
    await callback.answer()



# ─── /start ─────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user: dict) -> None:
    """
    Точка входу. UserMiddleware вже зареєстрував юзера в БД і передав його в `user`.
    Перевіряємо чи пройдений онбординг.
    """
    # Скидаємо будь-який попередній FSM стан (захист від "зависання" в середині потоку)
    await state.clear()

    if user.get("onboarded"):
        # Юзер повертається — показуємо зведення можливостей
        await message.answer(
            f"👋 З поверненням, <b>{message.from_user.first_name}</b>!\n\n"
            "Що хочеш зробити?\n\n"
            "💬 Просто напиши мені:\n"
            "  • <code>витратив 200 на каву</code>\n"
            "  • <code>отримав зарплату 30000</code>\n"
            "  • <code>чи можу я дозволити відпустку за 15000?</code>\n\n"
            "📊 /budget — фінансовий звіт\n"
            "🎯 /goals — мої цілі (Редагування)\n"
            "✏️ /history — останні транзакції\n"
            "❓ /help — довідка"
        )
        return

    # Новий юзер → запускаємо онбординг
    await state.set_state(OnboardingStates.choosing_method)
    await message.answer(
        f"👋 Привіт, <b>{message.from_user.first_name}</b>! Я — <b>FinanceOS</b>.\n\n"
        "Я допоможу тобі:\n"
        "• 📊 Вести бюджет у розмовному форматі\n"
        "• 🎯 Планувати накопичення на цілі\n"
        "• 🤔 Відповідати на питання про твої фінанси\n\n"
        "Щоб почати, мені потрібно знати трохи про твої фінанси.\n"
        "<b>Як хочеш заповнити початкові дані?</b>",
        reply_markup=kb_onboarding_method(),
    )


# ─── Вибір методу онбордингу ──────────────────────────────────────────────────

@router.callback_query(
    OnboardingStates.choosing_method,
    OnboardingAction.filter(F.action == "manual"),
)
async def onb_choose_manual(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Юзер обрав ручний ввід."""
    await callback.answer()
    await state.set_state(OnboardingStates.waiting_for_income)
    await callback.message.edit_text(  # type: ignore[union-attr]
        "✍️ Чудово! Давай почнемо з основного.\n\n"
        "💰 <b>Який твій середній місячний дохід?</b>\n\n"
        "<i>Введи суму числом (наприклад: 30000)</i>\n"
        "Можна вказати приблизно — за потреби змінимо пізніше."
    )


@router.callback_query(
    OnboardingStates.choosing_method,
    OnboardingAction.filter(F.action == "csv"),
)
async def onb_choose_csv(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Юзер обрав CSV — передаємо до CSVStates."""
    await callback.answer()
    await state.set_state(CSVStates.waiting_for_file)
    await callback.message.edit_text(  # type: ignore[union-attr]
        "📂 Чудово! Надішли мені виписку з банку.\n\n"
        "Підтримую формати:\n"
        "• <b>Monobank</b> → Натискаєте на обрану картку → Надіслати виписку за карткою → Обираєте період → Формат .pdf → Продовжити → Надіслати\n"
        "• <b>A-Bank</b> → Натискаєте на обрану картку → Виписка по картці → Обираєте період → Показати → Поділитись\n\n"
        "<i>⚠️ Дані залишаться тільки у тебе — я не передаю їх нікуди.</i>"
    )


# ─── Ввід доходу ────────────────────────────────────────────────────────────

@router.message(OnboardingStates.waiting_for_income, F.text)
async def onb_receive_income(
    message: Message,
    state: FSMContext,
    user: dict,
) -> None:
    """Отримуємо і валідуємо місячний дохід."""
    from bot.parsers import parse_natural_amount
    income = parse_natural_amount(message.text)

    if income is None or income <= 0:
        await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
        return

    if income > 10_000_000:
        await message.answer(
            "😅 Звучить занадто велике. Переконайся що ввів суму в <b>гривнях</b>."
        )
        return

    # Зберігаємо дохід у FSM context (не в БД ще — зберемо все разом наприкінці)
    await state.update_data(monthly_income=income)
    await state.set_state(OnboardingStates.waiting_for_comfort)

    await message.answer(
        f"✅ Зрозумів: <b>{fmt_amt(income)} грн/місяць</b>.\n\n"
        "🎚 <b>Який твій фінансовий стиль?</b>\n\n"
        "Це допоможе мені давати реалістичні поради — "
        "наприклад, скільки рекомендувати відкладати на цілі.",
        reply_markup=kb_comfort_level(),
    )


# ─── Вибір рівня комфорту ────────────────────────────────────────────────────

@router.callback_query(
    OnboardingStates.waiting_for_comfort,
    OnboardingAction.filter(F.action.startswith("comfort_")),
)
async def onb_receive_comfort(
    callback: CallbackQuery,
    callback_data: OnboardingAction,
    state: FSMContext,
    user: dict,
    db,
) -> None:
    """
    Отримуємо рівень комфорту і переходимо до вибору стилю спілкування.
    comfort_level зберігаємо як int 1-10 (кнопки 1-5 множимо на 2).
    """
    await callback.answer()

    comfort_raw = int(callback_data.action.split("_")[1])  # "comfort_3" → 3
    comfort_level = comfort_raw * 2  # 1-5 → 2-10 (для більшої гранулярності в БД)

    # Зберігаємо у FSM контекст
    await state.update_data(comfort_level=comfort_level, comfort_raw=comfort_raw)
    await state.set_state(OnboardingStates.waiting_for_style)

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"✅ Зрозумів твій стиль!\n\n"
        f"💬 <b>Як ти хочеш щоб я спілкувався?</b>\n\n"
        f"Це вплине на тон моїх відповідей — "
        f"від неформального друга до ділового консультанта. "
        f"Змінити можна будеть будь-коли.",
        reply_markup=kb_communication_style(),
    )


# ─── Вибір стилю спілкування ──────────────────────────────────────────────

@router.callback_query(
    OnboardingStates.waiting_for_style,
    OnboardingAction.filter(F.action.startswith("style_")),
)
async def onb_receive_style(
    callback: CallbackQuery,
    callback_data: OnboardingAction,
    state: FSMContext,
    user: dict,
    db,
) -> None:
    """
    Завершення онбордингу — зберігаємо всі зібрані дані в Supabase.
    """
    await callback.answer()

    style = callback_data.action.replace("style_", "")  # "style_casual" → "casual"

    # Дістаємо дані з FSM context
    fsm_data = await state.get_data()
    monthly_income = fsm_data.get("monthly_income", 0)
    comfort_level = fsm_data.get("comfort_level", 5)
    comfort_raw = fsm_data.get("comfort_raw", 3)

    # Зберігаємо в Supabase
    try:
        await repo.update_user(
            db=db,
            user_id=user["id"],
            monthly_income=monthly_income,
            comfort_level=comfort_level,
            communication_style=style,
            onboarded=True,
        )
    except Exception as e:
        logger.error(f"Failed to save onboarding data for user {user['id']}: {e}")
        await callback.message.answer(  # type: ignore[union-attr]
            "⚠️ Помилка збереження. Спробуй ще раз — /start"
        )
        await state.clear()
        return

    # Очищуємо FSM стан
    await state.clear()

    # Визначаємо текст відповідно до рівня комфорту
    comfort_emoji = ["😌", "🙂", "😐", "🧐", "💪"][comfort_raw - 1]
    style_labels = {
        "casual": "😎 Дружній",
        "balanced": "🙂 Збалансований",
        "formal": "👔 Офіційний",
    }

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"🎉 <b>Все готово!</b> Профіль налаштовано.\n\n"
        f"📌 Твої налаштування:\n"
        f"  💰 Дохід: <b>{fmt_amt(monthly_income)} грн/місяць</b>\n"
        f"  {comfort_emoji} Фінансовий стиль: <b>{comfort_raw}/5</b>\n"
        f"  💬 Стиль спілкування: <b>{style_labels.get(style, style)}</b>\n\n"
        f"Тепер просто пиши мені — я буду вести твій бюджет.\n\n"
        f"<b>Спробуй прямо зараз:</b>\n"
        f"<code>витратив 150 на обід</code>\n"
        f"<code>чи можу я купити ноутбук за 25000?</code>"
    )


# ─── /help ────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Довідка по командах та можливостях бота."""
    await message.answer(
        "❓ <b>FinanceOS — довідка</b>\n\n"
        "<b>Команди:</b>\n"
        "  /start — головне меню\n"
        "  /budget — фінансовий звіт за місяць\n"
        "  /goals — мої цілі накопичення (Редагування)\n"
        "  /history — останні транзакції (Редагування)\n"
        "  /style — змінити стиль спілкування AI\n"
        "  /clear — видалити всі мої дані\n"
        "  /help — ця довідка\n\n"
        "<b>Просто пиши у вільній формі:</b>\n"
        "  🔴 <code>витратив 200 на таксі</code>\n"
        "  🟢 <code>отримав зарплату 45000</code>\n"
        "  🔵 <code>скільки я витратив цього місяця?</code>\n"
        "  🎯 <code>хочу накопичити 20000 на відпустку</code>\n\n"
        "<b>Аналіз виписки:</b>\n"
        "  📂 Надішли PDF виписку з Monobank або A-Bank"
    )


# ─── /style (зміна стилю спілкування) ──────────────────────────────────────

@router.message(Command("style"))
async def cmd_style(message: Message, user: dict) -> None:
    """Дозволяє змінити стиль спілкування з AI."""
    current = user.get("communication_style", "balanced")
    style_labels = {
        "casual": "😎 Дружній",
        "balanced": "🙂 Збалансований",
        "formal": "👔 Офіційний",
    }
    await message.answer(
        f"💬 <b>Стиль спілкування</b>\n\n"
        f"Поточний стиль: <b>{style_labels.get(current, current)}</b>\n\n"
        f"Обери новий стиль — це вплине на тон моїх відповідей:",
        reply_markup=kb_communication_style(),
    )


@router.callback_query(OnboardingAction.filter(F.action.startswith("style_")))
async def handle_style_change(
    callback: CallbackQuery,
    callback_data: OnboardingAction,
    user: dict,
    db,
) -> None:
    """Обробляє зміну стилю спілкування через /style (поза онбордингом)."""
    style = callback_data.action.replace("style_", "")
    style_labels = {
        "casual": "😎 Дружній",
        "balanced": "🙂 Збалансований",
        "formal": "👔 Офіційний",
    }
    
    try:
        await callback.message.delete()
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)

    try:
        await repo.update_user(db, user["id"], communication_style=style)
        await callback.message.answer(
            f"✅ Стиль змінено на <b>{style_labels.get(style, style)}</b>.\n"
            f"Тепер мої відповіді будуть у новому тоні!"
        )
    except Exception as e:
        logger.error(f"Failed to update style for user {user['id']}: {e}")
        await callback.message.answer("⚠️ Не вдалось змінити стиль. Спробуй ще раз.")
    
    await callback.answer()


# ─── Захист від тексту в стані choosing_method ──────────────────────────────

@router.message(OnboardingStates.choosing_method)
async def onb_method_text_guard(message: Message) -> None:
    """Якщо юзер пише текст замість натискання кнопки."""
    await message.answer(
        "👆 Будь ласка, скористайся кнопками вище для вибору способу."
    )
