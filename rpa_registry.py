"""
RPA Registry — Thread-safe singleton registry for RPAController instances.

Purpose: LangGraph's SqliteSaver serializes the entire state dict via msgpack.
RPAController contains live subprocess handles which cannot be serialized.
This module keeps RPA instances alive in-process memory (outside the state dict),
referenced only by a string key. The key IS serializable.
"""

import threading
from typing import Optional

_lock = threading.Lock()
_registry: dict[str, "RPAController"] = {}  # noqa: F821


def get_rpa(key: str = "default") -> Optional["RPAController"]:  # noqa: F821
    """Retrieve a registered RPAController by key."""
    with _lock:
        return _registry.get(key)


def register_rpa(controller: "RPAController", key: str = "default") -> str:  # noqa: F821
    """Register an RPAController and return its key."""
    with _lock:
        _registry[key] = controller
    return key


def unregister_rpa(key: str = "default") -> None:
    """Remove a registered RPAController."""
    with _lock:
        _registry.pop(key, None)


def clear_all() -> None:
    """Clear all registered controllers (e.g., on shutdown)."""
    with _lock:
        _registry.clear()
