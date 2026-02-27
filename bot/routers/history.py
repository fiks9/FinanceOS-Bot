"""
Router для перегляду та редагування останніх транзакцій (Full Edit Mode).
"""
from aiogram import F, Router
from bot.utils import fmt_amt
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger

from ai.intent import extract_transaction, generate_confirmation
from bot.routers.ai_chat import _find_category_id, CONFIDENCE_THRESHOLD
from bot.states import EditTransactionStates
from database import repository as repo

from bot.keyboards import TransactionAction

router = Router(name="history")


@router.message(Command("history"))
async def cmd_history(message: Message, user: dict, db) -> None:
    """Виводить останні 3 транзакції для вибору дії (швидке меню)."""
    txs = await repo.get_recent_transactions(db, user["id"], limit=3)

    if not txs:
        await message.answer("📝 У вас ще немає збережених транзакцій.")
        return

    buttons = []
    for tx in txs:
        tx_id = tx["id"]
        amount = tx["amount"]
        t_type = tx["type"]
        desc = tx.get("description") or ""
        cat = tx.get("categories") or {}
        cat_name = cat.get("name", "Інше")
        cat_icon = cat.get("icon", "💸")

        sign = "↔️" if t_type == "transfer" else ("➖" if t_type == "expense" else "➕")
        
        btn_text = f"{sign} {fmt_amt(amount)} - {cat_icon} {cat_name}"
        if desc:
            btn_text += f" | {desc}"

        buttons.append([InlineKeyboardButton(
            text=btn_text, 
            callback_data=TransactionAction(action="select", txn_id=str(tx_id)).pack()
        )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("📝 <b>Останні 3 записи.</b> Натисни на будь-який для керування:", reply_markup=keyboard)


@router.callback_query(TransactionAction.filter(F.action == "select"))
async def handle_select_transaction(callback: CallbackQuery, callback_data: TransactionAction, user: dict, db) -> None:
    """Відкриває меню дій для конкретної транзакції."""
    try:
        await callback.message.delete()
    except Exception:
        pass

    tx = await repo.get_transaction(db, user["id"], callback_data.txn_id)
    if not tx:
        await callback.message.answer("⚠️ Транзакцію не знайдено.")
        await callback.answer()
        return

    amount = tx["amount"]
    t_type = tx["type"]
    desc = tx.get("description") or ""
    raw_date = tx.get("transaction_date", "")
    date_str = raw_date[:10] if raw_date else "Невідома дата"  # YYYY-MM-DD
    cat = tx.get("categories") or {}
    cat_name = cat.get("name", "Інше")
    cat_icon = cat.get("icon", "💸")

    sign = "↔️" if t_type == "transfer" else ("➖" if t_type == "expense" else "➕")
    
    text_info = f"📅 {date_str}\n"
    text_info += f"{sign} {fmt_amt(amount)} {cat_icon} {cat_name}"
    if desc:
        text_info += f"\n📝 Опис: {desc}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Редагувати", callback_data=TransactionAction(action="edit", txn_id=callback_data.txn_id).pack()),
            InlineKeyboardButton(text="🗑 Видалити", callback_data=TransactionAction(action="delete", txn_id=callback_data.txn_id).pack())
        ]
    ])

    await callback.message.answer(f"🧾 <b>Вибрано транзакцію:</b>\n\n{text_info}\n\nОберіть дію:", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(TransactionAction.filter(F.action == "delete"))
async def handle_delete_transaction(callback: CallbackQuery, callback_data: TransactionAction, user: dict, db) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass

    tx = await repo.get_transaction(db, user["id"], callback_data.txn_id)
    if not tx:
        await callback.message.answer("⚠️ Транзакцію не знайдено.")
        await callback.answer()
        return

    desc = tx.get("description") or tx.get("categories", {}).get("name", "Інше")
    raw_date = tx.get("transaction_date", "")
    date_str = raw_date[:10] if raw_date else ""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Так, видалити", callback_data=TransactionAction(action="delete_confirm", txn_id=callback_data.txn_id).pack()),
            InlineKeyboardButton(text="❌ Ні, скасувати", callback_data=TransactionAction(action="delete_cancel", txn_id=callback_data.txn_id).pack())
        ]
    ])

    await callback.message.answer(f"❓ Ви впевнені, що хочете видалити транзакцію:\n<b>{desc} ({fmt_amt(tx['amount'])} грн) за {date_str}?</b>", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(TransactionAction.filter(F.action == "delete_confirm"))
async def handle_delete_confirm(callback: CallbackQuery, callback_data: TransactionAction, user: dict, db) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass

    await repo.delete_transaction(db, user["id"], callback_data.txn_id)
    await callback.message.answer("🗑 <b>Транзакцію успішно видалено.</b>", parse_mode="HTML")
    
    # Повертаємо список як у /history, щоб було зручно
    await cmd_history(callback.message, user, db)
    await callback.answer()


@router.callback_query(TransactionAction.filter(F.action == "delete_cancel"))
async def handle_delete_cancel(callback: CallbackQuery, callback_data: TransactionAction, user: dict, db) -> None:
    callback_data.action = "select"
    await handle_select_transaction(callback, callback_data, user, db)


@router.callback_query(TransactionAction.filter(F.action == "edit"))
async def handle_edit_transaction_btn(callback: CallbackQuery, callback_data: TransactionAction, state: FSMContext) -> None:
    """Обробляє натискання на кнопку редагування транзакції."""
    tx_id = callback_data.txn_id
    
    await state.set_state(EditTransactionStates.waiting_for_edit_input)
    await state.update_data(editing_tx_id=tx_id)

    # Видаляємо повідомлення зі списком (або проміжним меню)
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_edit_tx")
    ]])

    await callback.message.answer(
        text=(
            "✏️ <b>Введіть нові дані для цього запису.</b>\n\n"
            "Напиши так, ніби ти створюєш її вперше, наприклад:\n"
            "<code>300 Таксі Уклон</code>\n\n"
            "<i>Зверніть увагу: старі значення суми, категорії та опису будуть повністю перезаписані.</i>"
        ),
        parse_mode="HTML",
        reply_markup=cancel_kb
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_edit_tx")
async def cancel_edit_transaction(callback: CallbackQuery, state: FSMContext) -> None:
    """Скидає стан редагування, якщо юзер передумав."""
    await state.clear()
    chat_id = callback.message.chat.id
    await callback.message.delete()
    await callback.bot.send_message(chat_id=chat_id, text="❌ Редагування скасовано.")
    await callback.answer()


@router.message(EditTransactionStates.waiting_for_edit_input, F.text)
async def process_transaction_edit(message: Message, state: FSMContext, user: dict, db) -> None:
    """Отримує новий текст, парсить і оновлює існуючу транзакцію."""
    data = await state.get_data()
    tx_id = data.get("editing_tx_id")
    
    if not tx_id:
        await state.clear()
        await message.answer("⚠️ Сесію редагування втрачено. Спробуй ще раз через /history")
        return

    text = message.text.strip()
    user_id = user["id"]

    from bot.parsers import parse_natural_amount
    parsed_amt = parse_natural_amount(text)
    if parsed_amt is not None:
        text = f"{text} (Сума: {parsed_amt})"

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # 1. Завантажуємо категорії
    try:
        categories = await repo.get_categories_for_user(db, user_id)
    except Exception as e:
        logger.error(f"Failed to load categories for {user_id}: {e}")
        categories = []

    # 2. Витягуємо дані (AI)
    try:
        txn = await extract_transaction(text, categories)
    except Exception as e:
        logger.error(f"Transaction extraction failed for user {user_id}: {e}")
        await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
        return

    if txn.amount <= 0:
        await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
        return

    if txn.confidence < CONFIDENCE_THRESHOLD:
        await message.answer(
            f"🤔 Схоже на транзакцію, але я не впевнений.\n\n"
            f"Ти мав на увазі: <b>{fmt_amt(txn.amount)} грн</b> на <b>{txn.category}</b>?\n"
            f"Напиши чіткіше."
        )
        return

    category_id = _find_category_id(categories, txn.category, txn.type)

    # 3. Оновлюємо БД
    try:
        await repo.update_transaction(
            db=db,
            tx_id=tx_id,
            amount=txn.amount,
            type=txn.type,
            category_id=category_id,
            description=txn.description,
            ignore_in_stats=txn.ignore_in_stats
        )
    except Exception as e:
        logger.error(f"Failed to update transaction {tx_id}: {e}")
        await message.answer("⚠️ Не вдалось оновити. Спробуй ще раз.")
        await state.clear()
        return

    # 4. Генеруємо підтвердження
    try:
        confirmation = await generate_confirmation(txn, "")
        confirm_text = f"✅ <b>Запис оновлено!</b>\n{confirmation}"
    except Exception as e:
        logger.warning(f"Confirmation LLM failed, using template: {e}")
        sign = "↔️" if txn.type == "transfer" else ("➖" if txn.type == "expense" else "➕")
        confirm_text = f"✅ Запис оновлено:\n{sign} {fmt_amt(txn.amount)} грн — {txn.category}"

    await state.clear()
    await message.answer(confirm_text)
