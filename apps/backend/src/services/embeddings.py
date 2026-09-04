import tiktoken
from openai import OpenAI

from src.config.settings import settings

EMBED_MODEL = "text-embedding-3-small"
MAX_BATCH_SIZE = 2048  # OpenAI's per-request input-count cap
MAX_BATCH_TOKENS = 200_000  # OpenAI's per-request cap is 300k; short-text callers (terms, blobs) never got
# close to it, so the count cap alone was enough until a caller started embedding full video transcripts —
# 2048 of those blew past 300k tokens in one request. Wide margin under the real 300k cap: a first attempt at
# 280k still 400'd at 302k actual — tiktoken's local count ran ~8% under the server's on casual transcript
# text (emoji, elongated words), so the margin needs to absorb that drift, not just rounding.
MAX_ITEM_TOKENS = 8000  # the model's real per-item cap is 8192; a transcript that runs over gets truncated,
# not rejected — a truncated-but-embedded transcript still dedupes reposts fine (see overview.py's
# _dedupe_map), and failing the whole batch over one long outlier item would be worse than losing its tail.

_client = OpenAI(api_key=settings.openai_api_key)
_encoding = tiktoken.encoding_for_model(EMBED_MODEL)


def embed(text: str) -> list[float]:
    response = _client.embeddings.create(model=EMBED_MODEL, input=text)
    return response.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embeds many texts in as few API calls as possible. Order-preserving.
    Chunks by both item count and token budget — a chunk under MAX_BATCH_SIZE
    items can still exceed the per-request token cap if the texts are long."""
    if not texts:
        return []

    vectors: list[list[float]] = []
    chunk: list[str] = []
    chunk_tokens = 0
    for text in texts:
        encoded = _encoding.encode(text)
        if len(encoded) > MAX_ITEM_TOKENS:
            text = _encoding.decode(encoded[:MAX_ITEM_TOKENS])
        n_tokens = min(len(encoded), MAX_ITEM_TOKENS)
        if chunk and (len(chunk) >= MAX_BATCH_SIZE or chunk_tokens + n_tokens > MAX_BATCH_TOKENS):
            response = _client.embeddings.create(model=EMBED_MODEL, input=chunk)
            vectors.extend(item.embedding for item in response.data)
            chunk, chunk_tokens = [], 0
        chunk.append(text)
        chunk_tokens += n_tokens
    if chunk:
        response = _client.embeddings.create(model=EMBED_MODEL, input=chunk)
        vectors.extend(item.embedding for item in response.data)
    return vectors


def _self_check() -> None:
    global MAX_BATCH_TOKENS, MAX_BATCH_SIZE

    calls: list[list[str]] = []

    class _FakeItem:
        embedding = [0.0]

    class _FakeResponse:
        def __init__(self, n: int):
            self.data = [_FakeItem() for _ in range(n)]

    def fake_create(model, input):
        calls.append(list(input))
        return _FakeResponse(len(input))

    orig_create, orig_tokens, orig_size = _client.embeddings.create, MAX_BATCH_TOKENS, MAX_BATCH_SIZE
    _client.embeddings.create = fake_create
    try:
        MAX_BATCH_TOKENS, MAX_BATCH_SIZE = 1, 100  # budget smaller than any single text forces a split every time
        vectors = embed_batch(["hello world", "another one", "third text"])
        assert len(vectors) == 3
        assert all(len(c) == 1 for c in calls), f"token budget not respected: {[len(c) for c in calls]}"

        calls.clear()
        MAX_BATCH_TOKENS, MAX_BATCH_SIZE = 100_000, 100
        vectors = embed_batch(["short"] * 10)
        assert len(vectors) == 10
        assert len(calls) == 1, "texts well under budget should still batch into one request"

        calls.clear()
        MAX_BATCH_TOKENS, MAX_BATCH_SIZE = 100_000, 3
        vectors = embed_batch(["short"] * 10)
        assert len(vectors) == 10
        assert [len(c) for c in calls] == [3, 3, 3, 1], "item-count cap should still apply"

        calls.clear()
        MAX_BATCH_TOKENS, MAX_BATCH_SIZE = 100_000, 100
        global MAX_ITEM_TOKENS
        orig_item_tokens = MAX_ITEM_TOKENS
        MAX_ITEM_TOKENS = 3
        try:
            vectors = embed_batch(["one two three four five six seven eight"])
            assert len(vectors) == 1
            assert len(_encoding.encode(calls[0][0])) <= MAX_ITEM_TOKENS, "oversized item should be truncated, not rejected"
        finally:
            MAX_ITEM_TOKENS = orig_item_tokens
    finally:
        _client.embeddings.create = orig_create
        MAX_BATCH_TOKENS, MAX_BATCH_SIZE = orig_tokens, orig_size

    print("embeddings self-check ok")


if __name__ == "__main__":
    _self_check()
