"""Aggregate router for API v1."""

from fastapi import APIRouter

from kodezart.api.v1.endpoints.agent import router as agent_router
from kodezart.api.v1.endpoints.health import router as health_router

v1_router = APIRouter()
v1_router.include_router(health_router, tags=["health"])
v1_router.include_router(agent_router, prefix="/agent", tags=["agent"])
