"""
MLflow proxy — forwards requests to the internal MLflow server so the browser
avoids CORS issues when calling MLflow's REST API directly.
"""

import os
import httpx
from fastapi import APIRouter, Request, Response

router = APIRouter()

MLFLOW_URL = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")


async def _proxy(path: str, request: Request) -> Response:
    url = f"{MLFLOW_URL}/{path}"
    params = dict(request.query_params)
    method = request.method
    body = await request.body()

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(
            method, url, params=params,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type="application/json",
    )


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def mlflow_proxy(path: str, request: Request):
    return await _proxy(f"api/2.0/mlflow/{path}", request)
