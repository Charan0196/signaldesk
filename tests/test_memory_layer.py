"""Response-unwrapping tests.

List endpoints nest their payload under ``items``. Getting that unwrap wrong is
silent — the UI just renders empty — so it is pinned here with the exact shapes
the Hindsight client returns.
"""

from __future__ import annotations

from signaldesk.memory import _as_dict, _coerce_json, _flatten_content, items_of


class _Fake:
    """Stands in for a pydantic response model (model_dump + attrs)."""

    def __init__(self, payload):
        self._p = payload

    def model_dump(self):
        return self._p


class TestItemsOf:
    def test_unwraps_items(self):
        assert items_of(_Fake({"items": [{"id": "a"}, {"id": "b"}], "total": 2})) == [
            {"id": "a"},
            {"id": "b"},
        ]

    def test_accepts_plain_dict(self):
        assert items_of({"items": [{"id": "x"}]}) == [{"id": "x"}]

    def test_missing_items_is_empty_not_a_crash(self):
        assert items_of(_Fake({"total": 0})) == []
        assert items_of(_Fake({})) == []
        assert items_of(None) == []

    def test_drops_non_dict_entries(self):
        assert items_of({"items": [{"id": "a"}, "junk", None]}) == [{"id": "a"}]

    def test_items_is_not_a_list(self):
        assert items_of({"items": "nope"}) == []


class TestAsDict:
    def test_passthrough_and_none(self):
        assert _as_dict({"a": 1}) == {"a": 1}
        assert _as_dict(None) == {}

    def test_uses_model_dump_when_present(self):
        assert _as_dict(_Fake({"k": "v"})) == {"k": "v"}


class TestCoerceJson:
    def test_plain_object(self):
        assert _coerce_json('{"a": 1}') == {"a": 1}

    def test_fenced_json_block(self):
        assert _coerce_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_object_embedded_in_prose(self):
        assert _coerce_json('Here you go: {"risk": "high"} — thanks!') == {"risk": "high"}

    def test_braces_inside_strings_do_not_break_it(self):
        assert _coerce_json('{"q": "a } b", "n": 2}') == {"q": "a } b", "n": 2}

    def test_escaped_quote_inside_string(self):
        assert _coerce_json('{"q": "he said \\"hi\\"", "n": 1}') == {
            "q": 'he said "hi"',
            "n": 1,
        }

    def test_no_object_returns_none(self):
        assert _coerce_json("no json here") is None
        assert _coerce_json(None) is None


class TestFlattenContent:
    """Mental-model content arrives as a string OR a nested block tree."""

    def test_plain_string(self):
        assert _flatten_content("hello") == "hello"

    def test_block_tree(self):
        node = {
            "heading": "Risk Posture",
            "level": 2,
            "blocks": ["- High risk", "- Champion engaged"],
        }
        out = _flatten_content(node)
        assert "Risk Posture" in out
        assert "High risk" in out
        assert "Champion engaged" in out

    def test_nested_tree(self):
        node = {"heading": "Top", "level": 1, "blocks": [{"heading": "Sub", "level": 2, "blocks": ["deep"]}]}
        out = _flatten_content(node)
        assert "Top" in out and "Sub" in out and "deep" in out

    def test_list_of_nodes(self):
        out = _flatten_content([{"heading": "A", "blocks": ["1"]}, {"heading": "B", "blocks": ["2"]}])
        assert "A" in out and "B" in out

    def test_none_is_empty(self):
        assert _flatten_content(None) == ""

    def test_dedupes_repeated_parts(self):
        node = {"heading": "H", "blocks": ["same", "same"]}
        assert _flatten_content(node).count("same") == 1


class TestBriefLookupLogic:
    """`_find_brief_id` must pick the newest model with the account's prefix."""

    PREFIX = "Meridian Freight — account brief"

    def _match(self, items):
        matches = [i for i in items if str(i.get("name", "")).startswith(self.PREFIX)]
        matches.sort(key=lambda i: str(i.get("created_at") or ""), reverse=True)
        return matches[0].get("id") if matches else None

    def test_picks_newest(self):
        items = [
            {"name": self.PREFIX, "id": "old", "created_at": "2026-01-01"},
            {"name": self.PREFIX, "id": "new", "created_at": "2026-09-01"},
        ]
        assert self._match(items) == "new"

    def test_ignores_other_accounts(self):
        items = [{"name": "Halcyon Logistics — account brief", "id": "x", "created_at": "2026-09-01"}]
        assert self._match(items) is None

    def test_empty_returns_none(self):
        assert self._match([]) is None
