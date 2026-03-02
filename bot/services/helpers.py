"""
Shared helpers для роутерів бота.

Винесені сюди щоб уникнути крос-імпортів між роутерами
(наприклад history.py імпортував напряму з ai_chat.py).
"""

# Поріг впевненості LLM — якщо нижче, питаємо юзера що він мав на увазі
CONFIDENCE_THRESHOLD = 0.6


def _find_goal_id(goals: list[dict], goal_name: str) -> str | None:
    """Точний або частковий пошук цілі за назвою."""
    name_q = goal_name.lower().strip()

    # 1. Точний збіг
    for g in goals:
        if g.get("name", "").lower().strip() == name_q:
            return g.get("id")

    # 2. Substring match в обидва боки
    for g in goals:
        db_name = g.get("name", "").lower().strip()
        if name_q in db_name or db_name in name_q:
            return g.get("id")

    return None


def _find_category_id(categories: list[dict], category_name: str, txn_type: str) -> str | None:
    """
    Шукає category_id за назвою та типом.
    Алгоритм (від точного до нечіткого):
      1. Точний збіг (case-insensitive)
      2. Одна назва є частиною іншої (наприклад LLM повертає 'Кава', в БД 'Кава/Снеки')
      3. Fallback: категорія з найбільшим збігом токенів
    """
    name_q = category_name.lower().strip()
    same_type = [c for c in categories if c.get("type") == txn_type]

    # 1. Точний збіг
    for cat in same_type:
        if cat.get("name", "").lower().strip() == name_q:
            return cat["id"]

    # 2. Substring match в обидва боки (LLM "кава" vs DB "Кава/Снеки")
    for cat in same_type:
        db_name = cat.get("name", "").lower().strip()
        if name_q in db_name or db_name in name_q:
            return cat["id"]

    # 3. Token overlap — хоча б одне слово збігається
    q_tokens = set(name_q.replace("/", " ").split())
    best_id, best_score = None, 0
    for cat in same_type:
        db_tokens = set(cat.get("name", "").lower().replace("/", " ").split())
        score = len(q_tokens & db_tokens)
        if score > best_score:
            best_score = score
            best_id = cat["id"]

    if best_score > 0:
        return best_id

    return None
