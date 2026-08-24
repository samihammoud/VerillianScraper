from openai import OpenAI

from src.config.settings import settings

EMBED_MODEL = "text-embedding-3-small"
MAX_BATCH_SIZE = 2048  # OpenAI's per-request input cap

_client = OpenAI(api_key=settings.openai_api_key)


def embed(text: str) -> list[float]:
    response = _client.embeddings.create(model=EMBED_MODEL, input=text)
    return response.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embeds many texts in as few API calls as possible. Order-preserving."""
    if not texts:
        return []

    vectors: list[list[float]] = []
    for start in range(0, len(texts), MAX_BATCH_SIZE):
        chunk = texts[start : start + MAX_BATCH_SIZE]
        response = _client.embeddings.create(model=EMBED_MODEL, input=chunk)
        vectors.extend(item.embedding for item in response.data)
    return vectors
