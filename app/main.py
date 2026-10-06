
from dotenv import load_dotenv
load_dotenv()   

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routes import router

app = FastAPI(
    title="QuickDrop – Rider Payout Dispute Agent",
    description="AI agent that handles delivery-rider payout disputes end-to-end.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}
