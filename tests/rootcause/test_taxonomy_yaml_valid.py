"""taxonomy.yaml structural validity — spec §8.3."""

from __future__ import annotations

from sampark.rootcause import load_taxonomy

_FIXED_TAXONOMY = {
    "insufficient_funds",
    "issuer_downtime",
    "mandate_expired",
    "authentication_drop",
    "price_hesitation",
    "intent_lost",
    "disputed",
    "unknown",
}


def test_taxonomy_matches_the_spec_fixed_taxonomy_exactly():
    taxonomy = load_taxonomy()
    assert set(taxonomy.taxonomy) == _FIXED_TAXONOMY


def test_default_is_unknown():
    taxonomy = load_taxonomy()
    assert taxonomy.default == "unknown"


def test_every_source_mapping_targets_only_taxonomy_values():
    taxonomy = load_taxonomy()
    for source_name, mapping in taxonomy.sources.items():
        for context_code, value in mapping.items():
            assert value in _FIXED_TAXONOMY, (
                f"{source_name}.{context_code} -> {value!r} is not in the "
                f"fixed taxonomy"
            )


def test_covers_the_four_canonical_sources():
    taxonomy = load_taxonomy()
    assert set(taxonomy.sources.keys()) == {
        "failed_payment",
        "abandoned_checkout",
        "mandate_failure",
        "overdue_invoice",
    }
