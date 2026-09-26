from __future__ import annotations

import copy
import json

import pytest

from face_id_verification.verification_hash import (
    CONFIDENCE_SCALE,
    LEGACY_SCHEMA_ID,
    SCHEMA_ID,
    FaceEvidence,
    MetadataEvidence,
    SearchEvidence,
    build_canonical_payload,
    canonical_bounding_box,
    canonicalize_payload,
    compute_legacy_verification_hash,
    compute_verification_hash,
    to_ppm,
)

GOLDEN_FACES = (
    FaceEvidence(
        bounding_box=(12, 34, 567, 890),
        detection_confidence=0.98765,
        embedding_hash="0x" + "ab" * 32,
    ),
)
GOLDEN_SEARCH = SearchEvidence(
    pages_found=2,
    full_matches=1,
    partial_matches=0,
    entities=(("Zulaikha Rahman", 0.9125), ("café society", 0.5)),
    best_guess_labels=("Zulaikha Rahman", "Nahid Rahman"),
    page_urls=("https://example.com/alpha", "https://example.com/beta"),
)
GOLDEN_METADATA = (
    MetadataEvidence("https://example.com/alpha", "Ünïcödé — Ttitle", "Example", False),
    MetadataEvidence("https://example.com/beta", None, None, True),
)

GOLDEN_CANONICAL_TEXT = (
    '{"faces":[{"bounding_box":[12,34,567,890],"detection_confidence_ppm":987650,'
    '"embedding_hash":"0xabababababababababababababababababababababababababababababababab"}],'
    '"image_content_hash":"0x1111111111111111111111111111111111111111111111111111111111111111",'
    '"metadata":[{"has_error":false,"platform":"Example","source_url":"https://example.com/alpha",'
    '"title":"\\u00dcn\\u00efc\\u00f6d\\u00e9 \\u2014 Ttitle"},'
    '{"has_error":true,"platform":null,"source_url":"https://example.com/beta","title":null}],'
    '"reverse_search":{"best_guess_labels":["Zulaikha Rahman","Nahid Rahman"],'
    '"entities":[{"description":"Zulaikha Rahman","score_ppm":912500},'
    '{"description":"caf\\u00e9 society","score_ppm":500000}],'
    '"full_matches":1,"page_urls":["https://example.com/alpha","https://example.com/beta"],'
    '"pages_found":2,"partial_matches":0},"schema":"mukhdax/v1"}'
)

GOLDEN_DIGEST = "0x4cd522cd9d66125c162ecddeaf6cc7e7c668c94fbf51aee5dc04e26ed9de4875"


def _payload(**overrides):
    kwargs = {
        "image_content_hash": "0x" + "11" * 32,
        "faces": GOLDEN_FACES,
        "reverse_search": GOLDEN_SEARCH,
        "metadata": GOLDEN_METADATA,
    }
    kwargs.update(overrides)
    return build_canonical_payload(**kwargs)


class TestGoldenVector:
    """A true golden vector: both expected values are hard-coded external constants.

    The digest was produced independently of this codebase, so these assertions fail if
    anyone changes field names, key ordering, Unicode escaping, separators, null handling,
    numeric representation, evidence ordering, or the hashing step.
    """

    def test_canonical_text_matches_exactly(self):
        assert canonicalize_payload(_payload()) == GOLDEN_CANONICAL_TEXT

    def test_digest_matches_exactly(self):
        assert compute_verification_hash(_payload()) == GOLDEN_DIGEST

    def test_canonical_text_is_pure_ascii(self):
        text = canonicalize_payload(_payload())
        text.encode("ascii")
        assert "\\u00dc" in text

    def test_canonical_text_is_already_in_canonical_form(self):
        assert (
            json.dumps(
                json.loads(GOLDEN_CANONICAL_TEXT),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            == GOLDEN_CANONICAL_TEXT
        )

    def test_schema_identifier_is_present(self):
        assert json.loads(GOLDEN_CANONICAL_TEXT)["schema"] == SCHEMA_ID == "mukhdax/v1"


class TestDeterminism:
    def test_identical_input_gives_identical_hash(self):
        assert compute_verification_hash(_payload()) == compute_verification_hash(_payload())

    def test_dict_insertion_order_does_not_affect_hash(self):
        payload = _payload()
        reordered = dict(reversed(list(payload.items())))
        assert list(reordered) != list(payload)
        assert canonicalize_payload(reordered) == canonicalize_payload(payload)
        assert compute_verification_hash(reordered) == compute_verification_hash(payload)

    def test_nested_insertion_order_does_not_affect_hash(self):
        payload = _payload()
        shuffled = copy.deepcopy(payload)
        shuffled["faces"] = [dict(reversed(list(face.items()))) for face in payload["faces"]]
        assert compute_verification_hash(shuffled) == compute_verification_hash(payload)

    def test_unicode_is_escaped_not_emitted_raw(self):
        text = canonicalize_payload(_payload())
        assert "Ünïcödé" not in text
        assert "café society" not in text
        assert "\\u00e9" in text

    def test_unicode_normalization_is_not_applied_and_is_documented(self):
        # Canonical JSON escapes; it does not normalize. Canonically equivalent but
        # differently encoded text therefore yields a different fingerprint. This is a
        # known, documented limitation, not an accident.
        composed = "\u00dcn\u00efc\u00f6d\u00e9 \u2014 Ttitle"
        decomposed = "U\u0308n\u00efc\u00f6d\u00e9 \u2014 Ttitle"
        assert composed != decomposed
        first = _payload(metadata=(MetadataEvidence("u", composed, "P", False),))
        second = _payload(metadata=(MetadataEvidence("u", decomposed, "P", False),))
        assert canonicalize_payload(first) != canonicalize_payload(second)
        assert compute_verification_hash(first) != compute_verification_hash(second)

    def test_hash_is_lowercase_hex_with_0x(self):
        digest = compute_verification_hash(_payload())
        assert digest.startswith("0x")
        assert len(digest) == 66
        assert digest == digest.lower()


class TestConfidenceQuantization:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.0, 0),
            (1.0, CONFIDENCE_SCALE),
            (0.5, 500000),
            (0.98765, 987650),
            (0.9125, 912500),
            (1.0 / 3.0, 333333),
        ],
    )
    def test_to_ppm(self, value, expected):
        assert to_ppm(value) == expected

    def test_to_ppm_is_an_integer(self):
        assert isinstance(to_ppm(0.123456789), int)

    def test_to_ppm_clamps_above_one(self):
        assert to_ppm(1.5) == CONFIDENCE_SCALE

    def test_to_ppm_clamps_below_zero(self):
        assert to_ppm(-0.2) == 0

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_to_ppm_non_finite_is_zero(self, value):
        assert to_ppm(value) == 0

    def test_confidence_never_serialized_as_float(self):
        text = canonicalize_payload(_payload())
        assert "0.98765" not in text
        assert "detection_confidence_ppm" in text

    def test_tiny_confidence_difference_below_ppm_is_absorbed(self):
        first = _payload(faces=(FaceEvidence((1, 2, 3, 4), 0.5000001, "0x" + "cd" * 32),))
        second = _payload(faces=(FaceEvidence((1, 2, 3, 4), 0.5000002, "0x" + "cd" * 32),))
        assert compute_verification_hash(first) == compute_verification_hash(second)

    def test_confidence_difference_above_ppm_changes_hash(self):
        first = _payload(faces=(FaceEvidence((1, 2, 3, 4), 0.5001, "0x" + "cd" * 32),))
        second = _payload(faces=(FaceEvidence((1, 2, 3, 4), 0.5002, "0x" + "cd" * 32),))
        assert compute_verification_hash(first) != compute_verification_hash(second)


class TestBoundingBox:
    def test_canonical_bounding_box_is_x1y1x2y2_integers(self):
        assert canonical_bounding_box((12, 34, 567, 890)) == [12, 34, 567, 890]

    def test_canonical_bounding_box_coerces_to_int(self):
        assert canonical_bounding_box((1.9, 2.9, 3.9, 4.9)) == [1, 2, 3, 4]

    def test_canonical_bounding_box_serializes_as_json_array(self):
        assert json.loads(canonicalize_payload(_payload()))["faces"][0]["bounding_box"] == [
            12,
            34,
            567,
            890,
        ]

    def test_bounding_box_change_changes_hash(self):
        first = _payload(faces=(FaceEvidence((1, 2, 3, 4), 0.5, "0x" + "cd" * 32),))
        second = _payload(faces=(FaceEvidence((1, 2, 4, 4), 0.5, "0x" + "cd" * 32),))
        assert compute_verification_hash(first) != compute_verification_hash(second)

    def test_bounding_box_order_is_significant(self):
        first = _payload(faces=(FaceEvidence((1, 2, 3, 4), 0.5, "0x" + "cd" * 32),))
        second = _payload(faces=(FaceEvidence((3, 4, 1, 2), 0.5, "0x" + "cd" * 32),))
        assert compute_verification_hash(first) != compute_verification_hash(second)


class TestEvidenceOrdering:
    def test_evidence_order_is_preserved_not_sorted(self):
        reversed_urls = SearchEvidence(
            pages_found=2,
            full_matches=1,
            partial_matches=0,
            entities=GOLDEN_SEARCH.entities,
            best_guess_labels=GOLDEN_SEARCH.best_guess_labels,
            page_urls=tuple(reversed(GOLDEN_SEARCH.page_urls)),
        )
        assert (
            compute_verification_hash(_payload(reverse_search=reversed_urls))
            != compute_verification_hash(_payload())
        )

    def test_ranked_label_order_is_significant(self):
        swapped = SearchEvidence(
            pages_found=2,
            full_matches=1,
            partial_matches=0,
            entities=GOLDEN_SEARCH.entities,
            best_guess_labels=tuple(reversed(GOLDEN_SEARCH.best_guess_labels)),
            page_urls=GOLDEN_SEARCH.page_urls,
        )
        assert (
            compute_verification_hash(_payload(reverse_search=swapped))
            != compute_verification_hash(_payload())
        )

    def test_entity_order_is_significant(self):
        swapped = SearchEvidence(
            pages_found=2,
            full_matches=1,
            partial_matches=0,
            entities=tuple(reversed(GOLDEN_SEARCH.entities)),
            best_guess_labels=GOLDEN_SEARCH.best_guess_labels,
            page_urls=GOLDEN_SEARCH.page_urls,
        )
        assert (
            compute_verification_hash(_payload(reverse_search=swapped))
            != compute_verification_hash(_payload())
        )

    def test_metadata_order_is_preserved(self):
        assert (
            compute_verification_hash(_payload(metadata=tuple(reversed(GOLDEN_METADATA))))
            != compute_verification_hash(_payload())
        )

    def test_face_order_is_preserved(self):
        two_faces = GOLDEN_FACES + (
            FaceEvidence((1, 1, 2, 2), 0.25, "0x" + "ef" * 32),
        )
        assert (
            compute_verification_hash(_payload(faces=two_faces))
            != compute_verification_hash(_payload(faces=tuple(reversed(two_faces))))
        )


class TestOptionalAndNullFields:
    def test_reverse_search_key_is_always_present(self):
        assert "reverse_search" in _payload(reverse_search=None)

    def test_absent_reverse_search_is_null(self):
        payload = _payload(reverse_search=None)
        assert payload["reverse_search"] is None
        assert '"reverse_search":null' in canonicalize_payload(payload)

    def test_absent_reverse_search_changes_hash(self):
        assert (
            compute_verification_hash(_payload(reverse_search=None))
            != compute_verification_hash(_payload())
        )

    def test_null_optional_metadata_values(self):
        text = canonicalize_payload(_payload())
        assert '"platform":null' in text
        assert '"title":null' in text

    def test_missing_title_differs_from_present_title(self):
        without = _payload(metadata=(MetadataEvidence("u", None, None, False),))
        with_title = _payload(metadata=(MetadataEvidence("u", "t", None, False),))
        assert compute_verification_hash(without) != compute_verification_hash(with_title)

    def test_empty_lists_are_distinct_from_null(self):
        empty = _payload(reverse_search=None, metadata=())
        assert compute_verification_hash(empty) != compute_verification_hash(
            _payload(reverse_search=None)
        )


class TestMeaningfulChanges:
    def test_image_hash_change_changes_hash(self):
        assert (
            compute_verification_hash(_payload(image_content_hash="0x" + "22" * 32))
            != compute_verification_hash(_payload())
        )

    def test_embedding_hash_change_changes_hash(self):
        changed = (FaceEvidence(GOLDEN_FACES[0].bounding_box, 0.98765, "0x" + "cd" * 32),)
        assert (
            compute_verification_hash(_payload(faces=changed))
            != compute_verification_hash(_payload())
        )

    def test_page_count_change_changes_hash(self):
        changed = SearchEvidence(
            pages_found=3,
            full_matches=1,
            partial_matches=0,
            entities=GOLDEN_SEARCH.entities,
            best_guess_labels=GOLDEN_SEARCH.best_guess_labels,
            page_urls=GOLDEN_SEARCH.page_urls,
        )
        assert (
            compute_verification_hash(_payload(reverse_search=changed))
            != compute_verification_hash(_payload())
        )

    def test_metadata_title_change_changes_hash(self):
        changed = (
            MetadataEvidence("https://example.com/alpha", "Other", "Example", False),
            GOLDEN_METADATA[1],
        )
        assert (
            compute_verification_hash(_payload(metadata=changed))
            != compute_verification_hash(_payload())
        )

    def test_metadata_error_flag_change_changes_hash(self):
        changed = (
            MetadataEvidence("https://example.com/alpha", "Ünïcödé — Ttitle", "Example", True),
            GOLDEN_METADATA[1],
        )
        assert (
            compute_verification_hash(_payload(metadata=changed))
            != compute_verification_hash(_payload())
        )

    def test_schema_identifier_is_part_of_the_fingerprint(self):
        payload = _payload()
        payload["schema"] = LEGACY_SCHEMA_ID
        assert compute_verification_hash(payload) != GOLDEN_DIGEST


class TestLegacyCompatibility:
    LEGACY_PAYLOAD = {
        "image_content_hash": "0x" + "11" * 32,
        "faces": [
            {
                "bounding_box": [12, 34, 567, 890],
                "detection_confidence": 0.98765,
                "embedding_hash": "0x" + "ab" * 32,
            }
        ],
        "metadata": [
            {
                "source_url": "https://example.com/alpha",
                "title": "Ünïcödé — Ttitle",
                "platform": "Example",
                "has_error": False,
            }
        ],
    }
    LEGACY_DIGEST = compute_legacy_verification_hash(LEGACY_PAYLOAD)

    def test_legacy_path_is_still_available(self):
        assert self.LEGACY_DIGEST.startswith("0x")
        assert len(self.LEGACY_DIGEST) == 66

    def test_legacy_digest_is_stable(self):
        assert compute_legacy_verification_hash(self.LEGACY_PAYLOAD) == self.LEGACY_DIGEST

    def test_legacy_serializes_floats_verbatim(self):
        assert "0.98765" in canonicalize_payload(self.LEGACY_PAYLOAD)

    def test_legacy_and_v1_differ_for_the_same_evidence(self):
        v1_equivalent = build_canonical_payload(
            image_content_hash="0x" + "11" * 32,
            faces=(
                FaceEvidence((12, 34, 567, 890), 0.98765, "0x" + "ab" * 32),
            ),
            reverse_search=None,
            metadata=(
                MetadataEvidence(
                    "https://example.com/alpha", "Ünïcödé — Ttitle", "Example", False
                ),
            ),
        )
        assert compute_verification_hash(v1_equivalent) != self.LEGACY_DIGEST

    def test_legacy_and_v1_use_different_serializations(self):
        assert canonicalize_payload(self.LEGACY_PAYLOAD) != canonicalize_payload(
            build_canonical_payload(
                image_content_hash="0x" + "11" * 32,
                faces=GOLDEN_FACES,
                reverse_search=None,
                metadata=(GOLDEN_METADATA[0],),
            )
        )

    def test_schema_ids_are_distinct(self):
        assert SCHEMA_ID != LEGACY_SCHEMA_ID
        assert LEGACY_SCHEMA_ID == "mukhdax/legacy-unversioned"

    def test_legacy_payload_has_no_schema_key(self):
        assert "schema" not in self.LEGACY_PAYLOAD

    def test_legacy_tuple_and_list_serialize_identically(self):
        with_tuple = {**self.LEGACY_PAYLOAD, "faces": [(12, 34, 567, 890)]}
        with_list = {**self.LEGACY_PAYLOAD, "faces": [[12, 34, 567, 890]]}
        assert compute_legacy_verification_hash(with_tuple) == compute_legacy_verification_hash(
            with_list
        )
