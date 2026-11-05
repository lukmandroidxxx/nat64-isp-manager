from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter as FastAPIAPIRouter
from fastapi import Request
from fastapi.routing import APIRoute
from starlette.responses import Response


class CommitAPIRoute(APIRoute):
    """Commit the request transaction before the response is sent.

    FastAPI exits ``yield`` dependencies (including ``get_db``) after the
    response has been sent. Without this hook a client that immediately re-reads
    after a write can observe the previous state, which breaks optimistic UI
    refreshes and read-after-write semantics. Committing here, before the
    response starts, makes the write visible as soon as the client receives it.

    Error responses roll back instead, matching ``get_db``'s failure behaviour.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original_handler(request)
            db = getattr(request.state, "db", None)
            if db is not None:
                if response.status_code < 400:
                    db.commit()
                else:
                    db.rollback()
            return response

        return handler


class APIRouter(FastAPIAPIRouter):
    """Router that uses :class:`CommitAPIRoute` by default."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("route_class", CommitAPIRoute)
        super().__init__(*args, **kwargs)


__all__ = ["APIRouter", "CommitAPIRoute"]
