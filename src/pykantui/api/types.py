"""Small closed types shared by outbound API clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Protocol, TypeAlias, TypeVar, cast, runtime_checkable

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic import JsonValue as PydanticJsonValue

from pykantui.api.errors import PayloadError


class HttpMethod(StrEnum):
    """HTTP methods used by provider clients."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | Mapping[str, "JsonValue"] | Sequence["JsonValue"]
JsonArray: TypeAlias = list[JsonValue]
JsonObject: TypeAlias = dict[str, JsonValue]
QueryScalar: TypeAlias = None | bool | int | float | str
QueryValue: TypeAlias = QueryScalar | list[QueryScalar] | tuple[QueryScalar, ...]
QueryParams: TypeAlias = Mapping[str, QueryValue]

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_JSON_ADAPTER: TypeAdapter[PydanticJsonValue] = TypeAdapter(PydanticJsonValue)


def ensure_json(value: object) -> JsonValue:
    """Validate an untrusted decoded response as ordinary JSON data."""
    try:
        return cast(JsonValue, _JSON_ADAPTER.validate_python(value))
    except ValidationError as error:
        raise PayloadError("provider returned a value that is not valid JSON") from error


def parse_json(value: JsonValue, model: type[_ModelT]) -> _ModelT:
    """Validate one provider response against its Pydantic wire model."""
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise PayloadError(f"provider response did not match {model.__name__}") from error


def expect_object(value: JsonValue, *, context: str = "provider response") -> JsonObject:
    """Require a decoded provider response to be a JSON object."""
    if not isinstance(value, dict):
        raise PayloadError(f"{context} must be a JSON object")
    return value


def expect_array(value: JsonValue, *, context: str = "provider response") -> JsonArray:
    """Require a decoded provider response to be a JSON array."""
    if not isinstance(value, list):
        raise PayloadError(f"{context} must be a JSON array")
    return value


def expect_object_array(value: JsonValue, *, context: str = "provider response") -> list[JsonObject]:
    """Require an array whose every item is a JSON object."""
    items = expect_array(value, context=context)
    objects: list[JsonObject] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise PayloadError(f"{context} array item {index} must be a JSON object")
        objects.append(item)
    return objects


@runtime_checkable
class JsonClient(Protocol):
    """Injectable JSON transport contract used by provider adapters."""

    def get(self, path: str, params: QueryParams | None = None, *, ttl: float = 0.0, label: str = "") -> JsonValue:
        """Read one JSON resource."""

    def post(self, path: str, body: JsonValue = None, params: QueryParams | None = None) -> JsonValue:
        """Create a resource without automatic replay."""

    def put(self, path: str, body: JsonValue = None, params: QueryParams | None = None) -> JsonValue:
        """Replace a resource without automatic replay."""

    def patch(self, path: str, body: JsonValue = None, params: QueryParams | None = None) -> JsonValue:
        """Partially update a resource without automatic replay."""

    def delete(self, path: str, params: QueryParams | None = None) -> JsonValue:
        """Delete a resource without automatic replay."""

    def graphql(self, query: str, variables: Mapping[str, JsonValue] | None = None, *, path: str = "") -> JsonValue:
        """Run one GraphQL operation and return its data value."""

    def close(self) -> None:
        """Release pooled network resources."""

__all__ = [
    "HttpMethod",
    "JsonArray",
    "JsonClient",
    "JsonObject",
    "JsonValue",
    "QueryParams",
    "QueryScalar",
    "QueryValue",
    "ensure_json",
    "expect_array",
    "expect_object",
    "expect_object_array",
    "parse_json",
]
