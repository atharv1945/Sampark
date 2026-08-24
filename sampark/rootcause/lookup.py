"""Root-cause lookup — spec §8.3, deterministic, and that is the point.

Loads the versioned YAML taxonomy (taxonomy.yaml, next to this module) and
exposes one pure function: (source, context_code) -> taxonomy string.
Anything unmapped resolves to `unknown` — never a guess, never an
exception. This is a dictionary, not a model; §9 explains why that is the
argument, not a gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

_TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"


class RootCauseTaxonomyError(ValueError):
    """taxonomy.yaml is malformed or violates its own invariants."""


@dataclass(frozen=True)
class RootCauseTaxonomy:
    version: int
    taxonomy: tuple[str, ...]
    default: str
    sources: Mapping[str, Mapping[str, str]]

    def lookup(self, source: str, context_code: str) -> str:
        return self.sources.get(source, {}).get(context_code, self.default)


def load_taxonomy(path: Path = _TAXONOMY_PATH) -> RootCauseTaxonomy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    taxonomy = tuple(raw["taxonomy"])
    default = raw["default"]
    sources = raw["sources"]

    if default not in taxonomy:
        raise RootCauseTaxonomyError(
            f"default {default!r} is not a member of taxonomy {taxonomy!r}"
        )

    for source_name, mapping in sources.items():
        for context_code, value in mapping.items():
            if value not in taxonomy:
                raise RootCauseTaxonomyError(
                    f"sources.{source_name}.{context_code} maps to "
                    f"{value!r}, which is not in taxonomy {taxonomy!r}"
                )

    return RootCauseTaxonomy(
        version=raw["version"],
        taxonomy=taxonomy,
        default=default,
        sources=sources,
    )


_DEFAULT_TAXONOMY: RootCauseTaxonomy | None = None


def classify(source: str, context_code: str) -> str:
    """Convenience entry point using the committed taxonomy.yaml."""
    global _DEFAULT_TAXONOMY
    if _DEFAULT_TAXONOMY is None:
        _DEFAULT_TAXONOMY = load_taxonomy()
    return _DEFAULT_TAXONOMY.lookup(source, context_code)
