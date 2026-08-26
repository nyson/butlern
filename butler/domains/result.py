from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeAlias, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ServiceError:
    """Expected, user-mappable failure from a service method."""

    code: str


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err:
    error: ServiceError


ServiceResult: TypeAlias = Ok[T] | Err


def ok(value: T) -> Ok[T]:
    return Ok(value)


def err(code: str) -> Err:
    return Err(ServiceError(code=code))
