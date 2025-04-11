from fastapi import APIRouter
from app.api.endpoints import extract

api_router = APIRouter()
api_router.include_router(extract.router, tags=["extraction"]) 