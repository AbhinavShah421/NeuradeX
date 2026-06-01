"""User-facing settings — currently market-data provider configuration."""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.data.providers import list_status, set_config

router = APIRouter()


class ProviderConfigUpdate(BaseModel):
    order:    Optional[list[str]] = None   # priority order of provider names
    disabled: Optional[list[str]] = None   # providers to skip
    keys:     Optional[dict] = None         # e.g. {"alphavantage": "KEY"}


@router.get("/providers")
async def get_providers(user: dict = Depends(get_current_user)):
    """Providers + availability + current config (order / enabled / keys present)."""
    return {"status": "success", "data": await list_status()}


@router.put("/providers")
async def update_providers(req: ProviderConfigUpdate, user: dict = Depends(get_current_user)):
    """Update provider order / enabled flags / API keys."""
    updates = {k: v for k, v in req.dict().items() if v is not None}
    await set_config(updates)
    return {"status": "success", "data": await list_status()}
