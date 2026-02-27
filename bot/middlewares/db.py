"""
DatabaseMiddleware — ін'єктує Supabase async клієнт в кожен апдейт.

Завдяки цьому хендлери отримують db через параметр `data['db']`.
"""
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from supabase import AsyncClient


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, db: AsyncClient) -> None:
        self.db = db
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Додаємо клієнт в data — доступний у всіх наступних middleware та хендлерах
        data["db"] = self.db
        return await handler(event, data)
