"""Engine adapters. Every engine is driven through the same interface (base.Engine)."""

from m0.engines.base import Engine, ExecResult


def get(name: str) -> Engine:
    if name == "trino":
        from m0.engines.trino import TrinoEngine

        return TrinoEngine()
    raise KeyError(f"no adapter for engine {name!r} yet")


def available() -> tuple[str, ...]:
    return ("trino",)


__all__ = ["Engine", "ExecResult", "available", "get"]
