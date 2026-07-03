# Canonical source for every service's app/cors.py.
# Do not edit the per-service copies directly — edit this file, then run
# `python scripts/sync_shared_python.py` to propagate the change.
"""Shared CORS setup for the internal microservices.

Every agent/service independently re-added the identical
`CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`
block. Wildcard CORS is intentional here — these are internal services not
exposed directly to browsers — this module just centralizes the boilerplate,
it does not change the policy.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def configure_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
