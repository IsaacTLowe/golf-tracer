from __future__ import annotations

from fastapi import Request
from tracer_core.config import Settings
from tracer_core.storage import Storage


def get_storage(request: Request) -> Storage:
    store: Storage = request.app.state.storage
    return store


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings
