"""
UserMiddleware — автоматично реєструє нового юзера в БД при першому зверненні.

Виконується після DatabaseMiddleware (db вже в data).
Додає об'єкт `user` (dict з Supabase) в data для всіх хендлерів.
"""
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from database.repository import get_or_create_user


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Дістаємо telegram-юзера з апдейту
        # event_from_user — aiogram 3.x атрибут який є в Update, Message, CallbackQuery...
        telegram_user = data.get("event_from_user")

        if telegram_user is not None and not telegram_user.is_bot:
            db = data["db"]
            # upsert: створює або повертає наявного юзера
            user = await get_or_create_user(
                db=db,
                tg_id=telegram_user.id,
                tg_username=telegram_user.username,
                full_name=telegram_user.full_name,
            )
            data["user"] = user  # Тепер хендлери мають доступ до data['user']

        return await handler(event, data)
