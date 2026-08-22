from fastapi import FastAPI

app = FastAPI(title="VerillianScraper Backend")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
