"""Deterministic root-cause classification — spec §8.3.

A versioned YAML lookup table, not a model. See lookup.py's module
docstring and taxonomy.yaml's header comment for why.
"""

from __future__ import annotations

from sampark.rootcause.lookup import (
    RootCauseTaxonomy,
    RootCauseTaxonomyError,
    classify,
    load_taxonomy,
)

__all__ = [
    "RootCauseTaxonomy",
    "RootCauseTaxonomyError",
    "classify",
    "load_taxonomy",
]
