from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.routes.topology import router as topology_router

app = FastAPI(title="VerillianScraper Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(topology_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
