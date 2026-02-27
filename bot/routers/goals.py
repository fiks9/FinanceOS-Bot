"""
Goals Router — управління фінансовими цілями.
"""
from aiogram import Router, F
from bot.utils import fmt_amt
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from datetime import datetime

from bot.keyboards import (
    kb_goals_manage_start, kb_goals_list, kb_goal_actions, 
    kb_goal_edit_options, kb_goal_delete_confirm, GoalManageAction
)
from bot.states import ManageGoalStates

from database import repository as repo

router = Router(name="goals")


def _generate_progress_bar(current: float, target: float, length: int = 10) -> str:
    if target <= 0:
        return "🟩" * length
    percent = current / target
    filled = int(percent * length)
    empty = length - filled
    if filled > length:
        filled = length
        empty = 0
    return "🟩" * filled + "⬜️" * empty


@router.message(Command("goals"))
async def cmd_goals(message: Message, user: dict, db) -> None:
    """Список активних цілей юзера з прогресом."""
    user_id = user["id"]
    try:
        goals = await repo.get_active_goals(db, user_id)
    except Exception as e:
        await message.answer("⚠️ Не вдалось завантажити список цілей.")
        return

    if not goals:
        await message.answer(
            "У тебе поки немає активних цілей.\n\n"
            "Напиши мені, наприклад:\n"
            "<code>хочу накопичити 20000 на ноутбук за 6 місяців</code>"
        )
        return

    text_lines = ["🎯 <b>Твої фінансові цілі:</b>\n"]
    
    for i, g in enumerate(goals, 1):
        name = g["name"]
        target = g["target_amount"]
        current = g["current_amount"]
        deposit = g["monthly_deposit"]
        deadline_str = g.get("deadline")
        
        progress_bar = _generate_progress_bar(current, target)
        percent = (current / target * 100) if target > 0 else 0
        
        lines = [
            f"<b>{i}. {name}</b>",
            f"{progress_bar} {percent:.1f}%",
            f"Зібрано: <b>{fmt_amt(current)} з {fmt_amt(target)} грн</b>"
        ]
        
        if deposit:
            lines.append(f"Внесок: <b>{fmt_amt(deposit)} грн/міс</b>")
            
        if deadline_str:
            try:
                deadline_date = datetime.fromisoformat(deadline_str).date()
                lines.append(f"Дедлайн: <b>{deadline_date.strftime('%d.%m.%Y')}</b>")
            except:
                pass
                
        text_lines.append("\n".join(lines))
        text_lines.append("")

    await message.answer("\n".join(text_lines), reply_markup=kb_goals_manage_start())


# ─── Inline button flow: Редагування цілей ───────────────────────────────────

@router.callback_query(GoalManageAction.filter(F.action == "list"))
async def handle_goal_manage_list(callback: CallbackQuery, user: dict, db):
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    goals = await repo.get_active_goals(db, user["id"])
    if not goals:
        await callback.answer("Активних цілей більше немає.", show_alert=True)
        return
        
    await callback.message.answer(
        "Натисніть на ціль щоб редагувати або видалити її", 
        reply_markup=kb_goals_list(goals)
    )
    await callback.answer()


@router.callback_query(GoalManageAction.filter(F.action == "select"))
async def handle_goal_manage_select(callback: CallbackQuery, callback_data: GoalManageAction, user: dict, db):
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    goals = await repo.get_active_goals(db, user["id"])
    goal = next((g for g in goals if str(g["id"]) == callback_data.goal_id), None)
    
    if not goal:
        await callback.answer("Цю ціль не знайдено.", show_alert=True)
        return
        
    await callback.message.answer(
        f"Ціль: <b>{goal['name']}</b>\nОберіть дію:", 
        reply_markup=kb_goal_actions(callback_data.goal_id)
    )
    await callback.answer()


@router.callback_query(GoalManageAction.filter(F.action == "edit"))
async def handle_goal_manage_edit(callback: CallbackQuery, callback_data: GoalManageAction):
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    await callback.message.answer(
        "Що ви хочете змінити?", 
        reply_markup=kb_goal_edit_options(callback_data.goal_id)
    )
    await callback.answer()


@router.callback_query(GoalManageAction.filter(F.action.in_({"edit_collected", "edit_target"})))
async def handle_goal_manage_edit_value(callback: CallbackQuery, callback_data: GoalManageAction, state: FSMContext):
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    await state.update_data(editing_goal_id=callback_data.goal_id)
    
    if callback_data.action == "edit_collected":
        await state.set_state(ManageGoalStates.waiting_for_new_collected)
        await callback.message.answer("Введіть нове значення для <b>зібраної суми</b> (тільки число):")
    else:
        await state.set_state(ManageGoalStates.waiting_for_new_target)
        await callback.message.answer("Введіть нове значення для <b>цільової суми</b> (тільки число):")
        
    await callback.answer()


@router.message(ManageGoalStates.waiting_for_new_collected, F.text)
async def handle_goal_edit_collected_input(message: Message, user: dict, db, state: FSMContext):
    data = await state.get_data()
    goal_id = data.get("editing_goal_id")
    await state.clear()
    
    if not goal_id:
        return
    from bot.parsers import parse_natural_amount
    new_amount = parse_natural_amount(message.text)
    
    if new_amount is None:
        if message.text.strip() == "0":
            new_amount = 0.0
        else:
            await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
            return
        
    await repo.update_goal(db, goal_id, user["id"], current_amount=new_amount)
    await message.answer("✅ <b>Зібрану суму успішно оновлено!</b>")
    await cmd_goals(message, user, db)


@router.message(ManageGoalStates.waiting_for_new_target, F.text)
async def handle_goal_edit_target_input(message: Message, user: dict, db, state: FSMContext):
    data = await state.get_data()
    goal_id = data.get("editing_goal_id")
    await state.clear()
    
    if not goal_id:
        return
    from bot.parsers import parse_natural_amount
    new_amount = parse_natural_amount(message.text)
    
    if new_amount is None:
        if message.text.strip() == "0":
            new_amount = 0.0
        else:
            await message.answer("⚠️ Не зрозумів суму. Спробуй написати так: 25000 або 25 тисяч")
            return
        
    await repo.update_goal(db, goal_id, user["id"], target_amount=new_amount)
    await message.answer("✅ <b>Цільову суму успішно оновлено!</b>")
    await cmd_goals(message, user, db)


@router.callback_query(GoalManageAction.filter(F.action == "delete"))
async def handle_goal_manage_delete(callback: CallbackQuery, callback_data: GoalManageAction, user: dict, db):
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    goals = await repo.get_active_goals(db, user["id"])
    goal = next((g for g in goals if str(g["id"]) == callback_data.goal_id), None)
    
    if not goal:
        await callback.answer("Ціль не знайдена.", show_alert=True)
        return
        
    await callback.message.answer(
        f"Ви впевнені що хочете видалити ціль <b>{goal['name']}</b>?", 
        reply_markup=kb_goal_delete_confirm(callback_data.goal_id)
    )
    await callback.answer()


@router.callback_query(GoalManageAction.filter(F.action == "cancel_delete"))
async def handle_goal_manage_cancel_delete(callback: CallbackQuery, callback_data: GoalManageAction, user: dict, db):
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    # Повертаємось на крок 3 (вибір дії для цілі)
    goals = await repo.get_active_goals(db, user["id"])
    goal = next((g for g in goals if str(g["id"]) == callback_data.goal_id), None)
    
    if not goal:
        await callback.answer("Ціль не знайдена.", show_alert=True)
        return
        
    await callback.message.answer(
        f"Ціль: <b>{goal['name']}</b>\nОберіть дію:", 
        reply_markup=kb_goal_actions(callback_data.goal_id)
    )
    await callback.answer()


@router.callback_query(GoalManageAction.filter(F.action == "confirm_delete"))
async def handle_goal_manage_confirm_delete(callback: CallbackQuery, callback_data: GoalManageAction, user: dict, db):
    try:
        await callback.message.delete()
    except Exception:
        pass
        
    await repo.delete_goal(db, callback_data.goal_id, user["id"])
    await callback.message.answer("✅ <b>Ціль успішно видалено.</b>")
    
    # Викликаємо cmd_goals (передаючи повідомлення з колбеку) щоб оновити список
    await cmd_goals(callback.message, user, db)
    await callback.answer()
