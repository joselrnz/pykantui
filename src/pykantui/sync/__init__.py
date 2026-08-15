"""Task stores, and the contract they all satisfy.

A backend maps whatever the store calls things onto the board's own model, so
the widgets never learn the difference between a local JSON file and Jira.
"""

from pykantui.sync.base import Backend
from pykantui.sync.jsonstore import JsonBackend, demo_backend

__all__ = [
    "Backend",
    "JsonBackend",
    "demo_backend",
]
