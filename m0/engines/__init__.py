"""Engine adapters. Every engine is driven through the same interface (base.Engine)."""

from m0.engines.base import Engine, ExecResult


def get(name: str) -> Engine:
    if name == "trino":
        from m0.engines.trino import TrinoEngine

        return TrinoEngine()
    if name in DF_ENGINES:
        from m0.engines.df import DfEngine

        return DfEngine(name)
    raise KeyError(f"no adapter for engine {name!r} yet")


# DataFusion engines driven by dfrunner single mode: `df` and its step 4c variants
# (m0/engines/df.py VARIANTS, kept in sync by a test).
DF_ENGINES = ("df", "df-cs", "df-jo", "df-jo-cs", "df-sail", "df-sail-cs")


def available() -> tuple[str, ...]:
    return ("trino", *DF_ENGINES)


__all__ = ["DF_ENGINES", "Engine", "ExecResult", "available", "get"]
