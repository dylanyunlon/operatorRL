"""
Macros — Common utility macros/helpers (Apollo parity).
=========================================================

Apollo reference: ``cyber/common/macros.h``

Provides RETURN_IF / RETURN_VAL_IF style early-return helpers,
singleton mixin, and other utility patterns used throughout Apollo.

Claude27: New file.
Location: lolbot-HyperAI/cyber/common/macros.py
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


def CYBER_RETURN_IF(condition: bool, msg: str = "") -> bool:
    """Apollo-style RETURN_IF macro as a function.

    Usage in component code::

        if CYBER_RETURN_IF(data is None, "No data"):
            return False

    Returns the condition value so caller can return early.
    """
    return condition


def CYBER_RETURN_VAL_IF(condition: bool, value: T, msg: str = "") -> Optional[T]:
    """Apollo-style RETURN_VAL_IF macro as a function.

    Usage::

        result = CYBER_RETURN_VAL_IF(not initialized, None, "Not init")
        if result is not None:
            return result
    """
    if condition:
        return value
    return None


class SingletonMixin:
    """Mixin that makes a class a thread-safe singleton.

    Apollo equivalent: ``DECLARE_SINGLETON(ClassName)`` macro.

    Usage::

        class MyManager(SingletonMixin):
            def __init__(self):
                super().__init__()
                self._data = {}

        mgr = MyManager.instance()
    """

    _instance: Optional[Any] = None
    _singleton_lock = threading.Lock()

    @classmethod
    def instance(cls) -> Any:
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._singleton_lock:
            cls._instance = None


def deprecated(reason: str = "") -> Callable:
    """Decorator to mark functions as deprecated.

    Apollo pattern: ALOG_MODULE_DEPRECATED warnings.
    """
    import functools
    import warnings

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(
                f"{func.__name__} is deprecated. {reason}",
                DeprecationWarning,
                stacklevel=2,
            )
            return func(*args, **kwargs)
        return wrapper
    return decorator
