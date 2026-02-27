import json
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from loguru import logger
from supabase import AsyncClient


class SupabaseStorage(BaseStorage):
    """
    Кастомне сховище FSM (сховище станів) для aiogram, яке використовує Supabase
    як єдине джерело істини відповідно до архітектури FinanceOS.
    Це дозволяє розгортати кілька інстансів бота (наприклад, на Railway)
    і не втрачати контекст під час перезавантажень контейнера.
    """

    def __init__(self, db: AsyncClient):
        self.db = db

    def _build_key(self, key: StorageKey) -> str:
        """Перетворює StorageKey в унікальний строковий ідентифікатор."""
        parts = [
            str(key.bot_id),
            str(key.chat_id),
            str(key.user_id),
            str(key.thread_id) if hasattr(key, "thread_id") and key.thread_id else "",
            str(key.business_connection_id) if hasattr(key, "business_connection_id") and key.business_connection_id else "",
            str(key.destiny),
        ]
        return ":".join(parts)

    async def _get_row(self, key_str: str) -> dict:
        """Повертає рядок (або порожній словник) з БД."""
        try:
            res = await self.db.table("fsm_states").select("*").eq("storage_key", key_str).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
        except Exception as e:
            logger.error(f"Failed to get FSM row for {key_str}: {e}")
        return {}

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        """Зберігає поточний стан FSM."""
        state_str = state.state if isinstance(state, State) else state
        key_str = self._build_key(key)

        try:
            row = await self._get_row(key_str)
            if row:
                await self.db.table("fsm_states").update({"state": state_str}).eq("storage_key", key_str).execute()
            else:
                await self.db.table("fsm_states").insert({"storage_key": key_str, "state": state_str}).execute()
        except Exception as e:
            logger.error(f"Failed to set FSM state for {key_str}: {e}")

    async def get_state(self, key: StorageKey) -> Optional[str]:
        """Отримує поточний стан FSM."""
        key_str = self._build_key(key)
        try:
            res = await self.db.table("fsm_states").select("state").eq("storage_key", key_str).execute()
            if res.data and len(res.data) > 0:
                return res.data[0].get("state")
        except Exception as e:
            logger.error(f"Failed to get FSM state for {key_str}: {e}")
        return None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        """Зберігає дані FSM."""
        key_str = self._build_key(key)
        try:
            row = await self._get_row(key_str)
            if row:
                await self.db.table("fsm_states").update({"data": data}).eq("storage_key", key_str).execute()
            else:
                await self.db.table("fsm_states").insert({"storage_key": key_str, "data": data}).execute()
        except Exception as e:
            logger.error(f"Failed to set FSM data for {key_str}: {e}")

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        """Отримує дані FSM."""
        key_str = self._build_key(key)
        try:
            res = await self.db.table("fsm_states").select("data").eq("storage_key", key_str).execute()
            if res.data and len(res.data) > 0:
                return res.data[0].get("data") or {}
        except Exception as e:
            logger.error(f"Failed to get FSM data for {key_str}: {e}")
        return {}

    async def close(self) -> None:
        """Закриває сховище (у нашому випадку нічого не закриваємо, supabase client управляється глобально)."""
        pass
