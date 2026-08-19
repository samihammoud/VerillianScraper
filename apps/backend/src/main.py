from fastapi import FastAPI

from src.routes.accounts import router as accounts_router

app = FastAPI(title="VerillianScraper Backend")
app.include_router(accounts_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
