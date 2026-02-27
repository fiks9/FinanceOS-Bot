"""
CRUD операції з базою даних Supabase.

Всі функції async і отримують supabase клієнт як параметр
(ін'єктується через DatabaseMiddleware).

Зараз: заглушки / скелет — буде заповнено на Кроці 2–3.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from supabase import AsyncClient


# ─── Users ─────────────────────────────────────────────────────────────────

async def get_or_create_user(
    db: AsyncClient,
    tg_id: int,
    tg_username: Optional[str],
    full_name: Optional[str],
) -> dict:
    """
    Повертає існуючого або створює нового юзера.
    Використовує upsert щоб уникнути race condition при одночасних запитах.
    """
    response = (
        await db.table("users")
        .upsert(
            {
                "tg_id": tg_id,
                "tg_username": tg_username,
                "full_name": full_name,
            },
            on_conflict="tg_id",       # Uniq constraint
            ignore_duplicates=False,   # Оновлюємо username/full_name якщо змінились
        )
        .execute()
    )
    return response.data[0]


async def get_user_by_tg_id(db: AsyncClient, tg_id: int) -> Optional[dict]:
    """Повертає юзера по Telegram ID або None якщо не знайдено."""
    response = (
        await db.table("users")
        .select("*")
        .eq("tg_id", tg_id)
        .maybe_single()
        .execute()
    )
    return response.data


async def update_user(db: AsyncClient, user_id: UUID, **kwargs) -> dict:
    """Оновлює поля профілю юзера."""
    response = (
        await db.table("users")
        .update(kwargs)
        .eq("id", str(user_id))
        .execute()
    )
    return response.data[0]


async def get_all_users(db: AsyncClient) -> list[dict]:
    """Отримує всіх юзерів (наприклад, для розсилок дайджестів)."""
    response = await db.table("users").select("*").execute()
    return response.data


async def delete_user(db: AsyncClient, user_id: UUID) -> None:
    """Видаляє юзера та всі його дані каскадно (ON DELETE CASCADE у БД)."""
    await db.table("users").delete().eq("id", str(user_id)).execute()


# ─── Transactions ───────────────────────────────────────────────────────────

async def _embed_and_save_transaction(db: AsyncClient, tx: dict):
    from ai.embeddings import generate_embedding
    text = f"Сума: {tx.get('amount')}. Тип: {tx.get('type')}. Опис: {tx.get('description', '')}"
    try:
        vector = await generate_embedding(text)
        await db.table("embeddings").insert({
            "user_id": tx["user_id"],
            "transaction_id": tx["id"],
            "content": text,
            "embedding": vector,
            "metadata": {"type": tx.get("type"), "amount": tx.get("amount")}
        }).execute()
    except Exception as e:
        import logging
        logging.error(f"Failed to save embedding: {e}")

async def add_transaction(db: AsyncClient, **kwargs) -> dict:
    """Додає нову транзакцію. kwargs повинен відповідати схемі таблиці transactions."""
    response = await db.table("transactions").insert(kwargs).execute()
    tx = response.data[0]
    import asyncio
    asyncio.create_task(_embed_and_save_transaction(db, tx))
    return tx

async def bulk_insert_transactions(db: AsyncClient, transactions: list[dict]) -> list[dict]:
    """Масовий інсерт транзакцій (для CSV імпорту). Один запит = весь список."""
    response = await db.table("transactions").insert(transactions).execute()
    inserted_txs = response.data
    import asyncio
    for tx in inserted_txs:
        asyncio.create_task(_embed_and_save_transaction(db, tx))
    return inserted_txs


# ─── Financial Snapshot & Vector Search ─────────────────────────────────────

async def search_similar_transactions(db: AsyncClient, user_id: UUID, query: str, limit: int = 5) -> list[dict]:
    """Векторний пошук по транзакціях юзера (match_embeddings RPC)."""
    from ai.embeddings import generate_embedding
    query_vector = await generate_embedding(query)
    
    response = (
        await db.rpc(
            "match_embeddings", 
            {
                "query_embedding": query_vector, 
                "p_user_id": str(user_id), 
                "match_count": limit
            }
        ).execute()
    )
    return response.data

async def get_monthly_balance(db: AsyncClient, user_id: UUID) -> dict:
    """
    Повертає агрегований баланс юзера за поточний місяць через VIEW.
    VIEW 'monthly_balance' вже є у schema.sql.
    """
    response = (
        await db.table("monthly_balance")
        .select("*")
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    if not response or not response.data:
        return {
            "total_income": 0,
            "total_expenses": 0,
            "net_balance": 0,
        }
    return response.data


async def get_spending_trends(db: AsyncClient, user_id: UUID, months: int = 3) -> list[dict]:
    """
    Повертає історію доходів і витрат за останні N місяців.
    Викликає RPC функцію 'get_spending_trends'.
    """
    response = (
        await db.rpc("get_spending_trends", {"p_user_id": str(user_id), "p_months": months})
        .execute()
    )
    return response.data


async def get_db_stats(db: AsyncClient, user_id: UUID) -> tuple[int, list[dict]]:
    """Повертає кількість тижнів від першої транзакції та всі витрати по категоріям за поточний місяць."""
    from datetime import datetime
    
    # 1. Отримуємо дату першої транзакції
    oldest_tx_resp = (
        await db.table("transactions")
        .select("transaction_date")
        .eq("user_id", str(user_id))
        .eq("ignore_in_stats", False)
        .order("transaction_date")
        .limit(1)
        .execute()
    )
    
    weeks_in_db = 0
    if oldest_tx_resp.data:
        try:
            first_date_str = oldest_tx_resp.data[0]["transaction_date"]
            # Parse datetime correctly considering timezone format
            if isinstance(first_date_str, str):
                # ISO format often has Z or +00:00
                first_date_str = first_date_str.split(".")[0].split("+")[0].replace("Z", "")
                first_date = datetime.fromisoformat(first_date_str)
                delta = datetime.now() - first_date
                weeks_in_db = max(0, delta.days // 7)
        except Exception:
            pass

    # 2. Витягуємо всі категорії витрат (не тільки топ 5)
    all_cat_resp = (
        await db.table("top_expense_categories")
        .select("*")
        .eq("user_id", str(user_id))
        .execute()
    )
    all_cats = all_cat_resp.data or []
    
    return weeks_in_db, all_cats


async def get_top_expense_categories(db: AsyncClient, user_id: UUID, limit: int = 5) -> list[dict]:
    """Топ категорій витрат за поточний місяць."""
    response = (
        await db.table("top_expense_categories")
        .select("*")
        .eq("user_id", str(user_id))
        .limit(limit)
        .execute()
    )
    return response.data


async def get_recent_transactions(db: AsyncClient, user_id: UUID, limit: int = 3) -> list[dict]:
    """Останні транзакції юзера для відображення в звіті."""
    response = (
        await db.table("transactions")
        .select("id, amount, type, description, categories(name, icon)")
        .eq("user_id", str(user_id))
        .order("transaction_date", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data


async def update_transaction(db: AsyncClient, tx_id: UUID, **kwargs) -> dict:
    """Оновлює існуючу транзакцію."""
    response = (
        await db.table("transactions")
        .update(kwargs)
        .eq("id", str(tx_id))
        .execute()
    )
    return response.data[0] if response.data else {}


async def get_transaction(db: AsyncClient, user_id: UUID, tx_id: UUID) -> dict | None:
    """Отримує одну транзакцію за id."""
    response = (
        await db.table("transactions")
        .select("id, amount, type, description, transaction_date, categories(name, icon)")
        .eq("id", str(tx_id))
        .eq("user_id", str(user_id))
        .maybe_single()
        .execute()
    )
    return response.data


async def delete_transaction(db: AsyncClient, user_id: UUID, tx_id: UUID) -> None:
    """Видаляє транзакцію з бази (hard delete)."""
    await (
        db.table("transactions")
        .delete()
        .eq("id", str(tx_id))
        .eq("user_id", str(user_id))
        .execute()
    )


async def get_active_goals(db: AsyncClient, user_id: UUID) -> list[dict]:
    """Всі активні цілі накопичення юзера."""
    response = (
        await db.table("goals")
        .select("id, name, target_amount, current_amount, monthly_deposit, deadline")
        .eq("user_id", str(user_id))
        .eq("status", "active")
        .execute()
    )
    return response.data


async def add_goal(
    db: AsyncClient,
    user_id: UUID,
    name: str,
    target_amount: float,
    deadline: str | None = None,
    monthly_deposit: float | None = None,
) -> dict:
    """Створює нову ціль."""
    payload = {
        "user_id": str(user_id),
        "name": name,
        "target_amount": target_amount,
        "status": "active",
    }
    if deadline:
        payload["deadline"] = deadline
    if monthly_deposit:
        payload["monthly_deposit"] = monthly_deposit

    response = (
        await db.table("goals")
        .insert(payload)
        .execute()
    )
    return response.data[0] if response.data else {}


async def update_goal_progress(db: AsyncClient, goal_id: UUID, amount: float) -> dict:
    """Додає amount до current_amount існуючої цілі (може бути від'ємним для зняття)."""
    # 1. Читаємо поточну ціль
    response = await db.table("goals").select("current_amount").eq("id", str(goal_id)).execute()
    if not response.data:
        return {}
    current = response.data[0].get("current_amount", 0)
    
    # 2. Оновлюємо
    new_amount = float(current) + amount
    update_response = (
        await db.table("goals")
        .update({"current_amount": new_amount})
        .eq("id", str(goal_id))
        .execute()
    )
    return update_response.data[0] if update_response.data else {}


async def update_goal(db: AsyncClient, goal_id: UUID, user_id: UUID, **kwargs) -> dict:
    """Оновлює довільні поля цілі (перевіряє user_id для безпеки)."""
    response = (
        await db.table("goals")
        .update(kwargs)
        .eq("id", str(goal_id))
        .eq("user_id", str(user_id))
        .execute()
    )
    return response.data[0] if response.data else {}


async def delete_goal(db: AsyncClient, goal_id: UUID, user_id: UUID) -> None:
    """Видаляє ціль (hard delete, тому що немає залежних зв'язків у transactions)."""
    await db.table("goals").delete().eq("id", str(goal_id)).eq("user_id", str(user_id)).execute()


# ─── Conversation Memory ────────────────────────────────────────────────────

async def get_recent_messages(
    db: AsyncClient,
    user_id: UUID,
    limit: int = 10,
) -> list[dict]:
    """Завантажує останні N повідомлень (включно з AI summary якщо є)."""
    response = (
        await db.table("conversation_memory")
        .select("role, content, is_summary")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    # Повертаємо у хронологічному порядку (старі спочатку)
    return list(reversed(response.data))


async def save_message(
    db: AsyncClient,
    user_id: UUID,
    role: str,
    content: str,
    token_count: int = 0,
    is_summary: bool = False,
) -> dict:
    """Зберігає одне повідомлення в пам'ять."""
    response = (
        await db.table("conversation_memory")
        .insert({
            "user_id": str(user_id),
            "role": role,
            "content": content,
            "token_count": token_count,
            "is_summary": is_summary,
        })
        .execute()
    )
    return response.data[0]


async def get_categories_for_user(db: AsyncClient, user_id: UUID) -> list[dict]:
    """
    Повертає категорії: глобальні (user_id IS NULL) + кастомні юзера.
    """
    response = (
        await db.table("categories")
        .select("id, name, type, icon")
        .or_(f"user_id.is.null,user_id.eq.{user_id}")
        .execute()
    )
    return response.data
