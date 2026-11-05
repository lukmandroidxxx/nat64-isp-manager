from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class AppError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: Any = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message, "detail": detail},
        )
        self.code = code
        self.message = message


class NotFound(AppError):
    def __init__(self, resource: str, identifier: Any = None) -> None:
        super().__init__(
            status.HTTP_404_NOT_FOUND,
            "not_found",
            f"{resource} not found",
            {"identifier": identifier},
        )


class Conflict(AppError):
    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(status.HTTP_409_CONFLICT, "conflict", message, detail)


class ValidationFailed(AppError):
    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_ENTITY, "validation_failed", message, detail)


class HostUnavailable(AppError):
    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, "host_unavailable", message, detail)


class NotImplementedOnHost(AppError):
    def __init__(self, feature: str, reason: str | None = None) -> None:
        super().__init__(
            status.HTTP_501_NOT_IMPLEMENTED,
            "not_implemented",
            f"{feature} is not implemented on this host",
            {"reason": reason or "The host integration is unavailable (non-Linux or helper not installed)"},
        )


class PermissionDenied(AppError):
    def __init__(self, message: str = "Insufficient permissions") -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, "permission_denied", message)


class Unauthorized(AppError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, "unauthorized", message)
