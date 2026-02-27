"""
Supabase async client — єдина точка підключення до БД.

Використовуємо supabase-py через REST API (PostgREST),
що автоматично дає connection pooling і не вичерпує
ліміти прямих Postgres-з'єднань на Free Tier.
"""
from supabase import AsyncClient, acreate_client

from bot.config import get_settings

# Глобальний async-клієнт (ініціалізується один раз при старті бота)
_supabase_client: AsyncClient | None = None


async def get_supabase() -> AsyncClient:
    """
    Повертає singleton async-клієнт Supabase.
    При першому виклику ініціалізує підключення.
    """
    global _supabase_client
    if _supabase_client is None:
        settings = get_settings()
        _supabase_client = await acreate_client(
            supabase_url=settings.supabase_url,
            supabase_key=settings.supabase_service_key,
        )
    return _supabase_client
