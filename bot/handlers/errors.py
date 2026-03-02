from __future__ import annotations

from aiogram import Router
from aiogram.types import ErrorEvent
from loguru import logger

router = Router(name="global_errors")


def _extract_update_context(event: ErrorEvent) -> tuple[int | None, int | None]:
    update = event.update
    update_id = getattr(update, "update_id", None)

    user_id = None
    message = getattr(update, "message", None)
    if message and getattr(message, "from_user", None):
        user_id = message.from_user.id
    else:
        callback_query = getattr(update, "callback_query", None)
        if callback_query and getattr(callback_query, "from_user", None):
            user_id = callback_query.from_user.id
    return update_id, user_id


@router.errors()
async def handle_global_error(event: ErrorEvent) -> None:
    update_id, user_id = _extract_update_context(event)
    logger.exception(
        "Unhandled bot error | update_id={} user_id={} error={}",
        update_id,
        user_id,
        event.exception,
    )

    try:
        if getattr(event.update, "message", None):
            await event.update.message.answer("⚠️ Сталась внутрішня помилка. Спробуй ще раз пізніше.")
        elif getattr(event.update, "callback_query", None):
            await event.update.callback_query.answer(
                "⚠️ Сталась внутрішня помилка. Спробуй ще раз пізніше.",
                show_alert=True,
            )
    except Exception as exc:
        logger.warning("Failed to deliver fallback error message: {}", exc)

