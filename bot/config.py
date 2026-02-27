"""
Centralized configuration via Pydantic Settings v2.
Читає значення з .env файлу або змінних середовища.

ВАЖЛИВО: НЕ ініціалізуємо settings на рівні модуля — тільки через get_settings().
Це дозволяє імпортувати модуль без .env файлу (для тестів, перевірки синтаксису).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ігнорувати невідомі змінні
    )

    # Telegram
    bot_token: str

    # Groq
    groq_api_key: str
    groq_model_smart: str = "llama-3.3-70b-versatile"
    groq_model_fast: str = "llama-3.1-8b-instant"

    # Supabase
    supabase_url: str
    supabase_service_key: str

    # App
    log_level: str = "INFO"
    environment: str = "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Lazy singleton — читає .env тільки при першому виклику.
    Кешується на весь час роботи програми.
    """
    return Settings()  # type: ignore[call-arg]


# Зручний аліас для зворотної сумісності в коді бота
# Перший виклик ініціалізує Settings і перевіряє наявність всіх ключів
settings = None  # type: ignore[assignment]
# Буде замінено в bot/run.py: from bot.config import get_settings; settings = get_settings()
