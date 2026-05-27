"""Workflow definitions live as YAML in this package; ``handlers.py`` registers
the deterministic Python callables they can invoke.
"""
from workflows import handlers  # noqa: F401  — register handlers at import

__all__ = []
