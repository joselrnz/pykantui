"""Place provider response caches in user state, isolated by workspace."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Mapping
from pathlib import Path

from pykantui.api import ResponseCache
from pykantui.config.paths import cache_path, ensure_private_directory, ensure_private_file
from pykantui.core.naming import safe_name
from pykantui.tracker.models import RemoteProject

_PROCESS_CACHE_KEY = secrets.token_bytes(32)
_CACHE_KEY_FILE = ".cache-identity-key"


def workspace_cache(
    workspace: Path,
    provider: str,
    project: RemoteProject,
    *,
    credentials: Mapping[str, str] | None = None,
) -> ResponseCache:
    """Return a global cache scope that cannot leak across local workspaces.

    A provider/project pair is not an authorization boundary: two credentials
    can see different fields on the same remote project.  The canonical local
    workspace path and an installation-keyed credential identity therefore
    contribute a short opaque namespace. No token, reversible token encoding,
    or provider response is included in a cache key.
    """

    canonical = str(workspace.expanduser().resolve(strict=False))
    identity = "\0".join(
        (
            canonical,
            provider.casefold(),
            project.project_id,
            project.owner,
            project.url,
            _credential_identity(credentials or {}),
        )
    )
    workspace_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    project_label = safe_name(project.slug()) or "project"
    namespace = f"{project_label}-{workspace_id}"
    ensure_private_directory(cache_path())
    return ResponseCache(cache_path()).scope(provider, namespace)


def _credential_identity(credentials: Mapping[str, str]) -> str:
    """Create a stable, non-reversible identity for one credential generation."""

    digest = hmac.new(_installation_cache_key(), digestmod=hashlib.sha256)
    for name, value in sorted(credentials.items()):
        encoded_name = name.encode("utf-8")
        encoded_value = value.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(encoded_value).to_bytes(8, "big"))
        digest.update(encoded_value)
    return digest.hexdigest()[:16]


def _installation_cache_key() -> bytes:
    """Load or exclusively create the private key used only for cache identity."""

    target = cache_path().parent / _CACHE_KEY_FILE
    try:
        if target.is_symlink():
            return _PROCESS_CACHE_KEY
        existing = target.read_bytes()
        if len(existing) == 32:
            ensure_private_file(target)
            return existing
    except FileNotFoundError:
        pass
    except OSError:
        return _PROCESS_CACHE_KEY

    try:
        ensure_private_directory(target.parent)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, _PROCESS_CACHE_KEY)
        finally:
            os.close(descriptor)
    except FileExistsError:
        pass
    except OSError:
        return _PROCESS_CACHE_KEY

    try:
        stored = target.read_bytes()
        ensure_private_file(target)
    except OSError:
        return _PROCESS_CACHE_KEY
    return stored if len(stored) == 32 else _PROCESS_CACHE_KEY


__all__ = ["workspace_cache"]
