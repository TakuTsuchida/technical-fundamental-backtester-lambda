from __future__ import annotations

from shared.dispatcher_service import DispatcherDeps, DispatcherService, MSG_TYPE_FINS

__all__ = ["DispatcherDeps", "FinsDispatcherService"]


class FinsDispatcherService(DispatcherService):
    def __init__(self, deps: DispatcherDeps) -> None:
        super().__init__(deps, msg_type=MSG_TYPE_FINS)
