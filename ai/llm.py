"""
LLM фабрика — два ChatGroq клієнти з чітким розподілом задач.

SMART (llama-3.3-70b-versatile):
  - Intent Detection
  - Відповіді на фінансові питання
  - Категоризація CSV транзакцій
  - Розрахунок плану накопичень

FAST (llama-3.1-8b-instant):
  - Підтвердження збереженої транзакції (короткий текст)
  - Форматування готових даних у відповідь
  - Навігаційні підказки

Назви моделей беруться виключно з Settings — не хардкодяться.
"""
from functools import lru_cache

from langchain_groq import ChatGroq

from bot.config import get_settings


@lru_cache(maxsize=1)
def get_smart_llm() -> ChatGroq:
    """
    Повертає LLM для задач де важлива якість міркування.
    Singleton — ініціалізується один раз.
    """
    s = get_settings()
    return ChatGroq(
        api_key=s.groq_api_key,
        model=s.groq_model_smart,
        temperature=0.1,   # Низька температура = детермінований, передбачуваний вивід
        max_tokens=1024,
    )


@lru_cache(maxsize=1)
def get_fast_llm() -> ChatGroq:
    """
    Повертає LLM для швидких задач форматування.
    Singleton — ініціалізується один раз.
    """
    s = get_settings()
    return ChatGroq(
        api_key=s.groq_api_key,
        model=s.groq_model_fast,
        temperature=0.3,   # Трохи вище — щоб підтвердження звучали природно
        max_tokens=256,
    )
