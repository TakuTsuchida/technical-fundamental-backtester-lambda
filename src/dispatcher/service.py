from __future__ import annotations

from shared.dispatcher_service import DispatcherDeps, DispatcherService

__all__ = ["DispatcherDeps", "PriceDispatcherService"]


class PriceDispatcherService(DispatcherService):
    def __init__(self, deps: DispatcherDeps) -> None:
        super().__init__(deps)
