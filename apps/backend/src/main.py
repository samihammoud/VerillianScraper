from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.routes.topology import router as topology_router
from src.routes.worlds import router as worlds_router

app = FastAPI(title="VerillianScraper Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "PUT"],
    allow_headers=["*"],
)

app.include_router(topology_router)
app.include_router(worlds_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
