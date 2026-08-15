"""Cross-platform names for files and cache path segments."""

from __future__ import annotations


def safe_name(value: str) -> str:
    """Reduce ``value`` to one safe path segment on every supported OS."""
    cleaned = "".join(
        "-" if character in '<>:"/\\|?*' or ord(character) < 32 else character for character in value
    )
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return "untitled"

    stem = cleaned.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{number}" for number in range(1, 10)} | {
        f"LPT{number}" for number in range(1, 10)
    }
    if stem in reserved:
        cleaned = f"_{cleaned}"
    return cleaned[:120]


__all__ = ["safe_name"]
