import asyncio
from typing import List
from sentence_transformers import SentenceTransformer

_model = None

def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        # Завантажуємо локальну NLP модель (384 виміри)
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model

async def generate_embedding(text: str) -> List[float]:
    """
    Генерує векторне представлення тексту в окремому потоці, 
    щоб не блокувати aiogram event loop.
    """
    model = _get_model()
    embedding = await asyncio.to_thread(model.encode, text)
    return embedding.tolist()
