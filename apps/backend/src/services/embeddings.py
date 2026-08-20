from openai import OpenAI

from src.config.settings import settings

EMBED_MODEL = "text-embedding-3-small"

_client = OpenAI(api_key=settings.openai_api_key)


def embed(text: str) -> list[float]:
    response = _client.embeddings.create(model=EMBED_MODEL, input=text)
    return response.data[0].embedding
