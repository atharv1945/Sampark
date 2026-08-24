"""One test case per taxonomy.yaml entry — spec §8.3's explicit requirement.

Parametrized over every (source, context_code) -> taxonomy_value entry
actually present in the committed YAML, so a new entry added to the file
gets a corresponding pass/fail case for free, and a typo'd taxonomy value
fails here rather than silently reclassifying real data.
"""

from __future__ import annotations

import pytest

from sampark.rootcause import RootCauseTaxonomyError, classify, load_taxonomy

_taxonomy = load_taxonomy()
_ALL_ENTRIES = [
    (source, context_code, expected)
    for source, mapping in _taxonomy.sources.items()
    for context_code, expected in mapping.items()
]


@pytest.mark.parametrize("source,context_code,expected", _ALL_ENTRIES)
def test_yaml_entry(source: str, context_code: str, expected: str) -> None:
    assert classify(source, context_code) == expected


def test_unmapped_context_code_falls_to_unknown():
    assert classify("failed_payment", "SOME_CODE_NOT_IN_THE_TABLE") == "unknown"


def test_unmapped_source_falls_to_unknown():
    assert classify("some_future_source", "ANYTHING") == "unknown"


def test_lookup_never_raises_on_unmapped_input():
    # The whole point of the fallback: an unmapped context is data
    # (counted as unknown), never an exception on the ingestion path.
    result = classify("failed_payment", "TOTALLY_UNRECOGNIZED")
    assert result == "unknown"


def test_load_taxonomy_rejects_a_default_outside_the_taxonomy(tmp_path):
    bad_yaml = tmp_path / "bad_taxonomy.yaml"
    bad_yaml.write_text(
        "version: 1\n"
        "taxonomy: [unknown]\n"
        "default: not_in_taxonomy\n"
        "sources: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(RootCauseTaxonomyError):
        load_taxonomy(bad_yaml)


def test_load_taxonomy_rejects_a_mapped_value_outside_the_taxonomy(tmp_path):
    bad_yaml = tmp_path / "bad_taxonomy.yaml"
    bad_yaml.write_text(
        "version: 1\n"
        "taxonomy: [unknown]\n"
        "default: unknown\n"
        "sources:\n"
        "  failed_payment:\n"
        "    SOME_CODE: not_in_taxonomy\n",
        encoding="utf-8",
    )
    with pytest.raises(RootCauseTaxonomyError):
        load_taxonomy(bad_yaml)
