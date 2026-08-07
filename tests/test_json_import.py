"""Tests for building rm files from JSON highlights."""

import io
from pathlib import Path

import pytest

from rmc.exporters.json_export import scene_to_dict
from rmc.importers.json_import import json_to_blocks, json_to_rm

try:
    from rmscene import (
        CrdtId,
        SceneGlyphItemBlock,
        SceneItemBlock,
        read_blocks,
        read_tree,
    )
    from rmscene import scene_items as si
except ImportError:
    pytest.skip("rmscene not installed", allow_module_level=True)

RM_DIR = Path(__file__).parent / "rm"
HIGHLIGHTED = RM_DIR / "Wikipedia_highlighted_p1.rm"

ONE_HIGHLIGHT = {
    "highlights": [
        {
            "text": "appended highlight",
            "start": None,
            "length": 18,
            "color": "BLUE",
            "color_rgba": None,
            "rectangles": [{"x": -800.0, "y": 900.0, "w": 500.0, "h": 56.0}],
        }
    ]
}


def build_rm(data, base_path=None) -> bytes:
    out = io.BytesIO()
    if base_path is None:
        json_to_rm(data, out)
    else:
        with open(base_path, "rb") as base:
            json_to_rm(data, out, base=base)
    return out.getvalue()


def export_highlights(rm_bytes: bytes) -> list:
    return scene_to_dict(read_tree(io.BytesIO(rm_bytes)))["highlights"]


def glyph_items(rm_bytes: bytes) -> list:
    return [
        block.item
        for block in read_blocks(io.BytesIO(rm_bytes))
        if isinstance(block, SceneGlyphItemBlock)
    ]


class TestNewPage:
    """A page built from scratch, for a PDF page with no annotations yet."""

    def test_produces_readable_rm(self):
        assert export_highlights(build_rm(ONE_HIGHLIGHT))

    def test_highlight_survives(self):
        highlight = export_highlights(build_rm(ONE_HIGHLIGHT))[0]
        assert highlight["text"] == "appended highlight"
        assert highlight["color"] == "BLUE"
        assert highlight["length"] == 18
        assert highlight["rectangles"] == [
            {"x": -800.0, "y": 900.0, "w": 500.0, "h": 56.0}
        ]

    def test_start_may_be_absent(self):
        """Since reMarkable 3.6 `start` is optional, and no external producer knows it."""
        assert export_highlights(build_rm(ONE_HIGHLIGHT))[0]["start"] is None

    def test_multiple_highlights_are_chained_in_order(self):
        data = {
            "highlights": [
                dict(ONE_HIGHLIGHT["highlights"][0], text="first"),
                dict(ONE_HIGHLIGHT["highlights"][0], text="second"),
            ]
        }
        items = glyph_items(build_rm(data))
        assert [item.value.text for item in items] == ["first", "second"]
        assert items[1].left_id == items[0].item_id

    def test_empty_highlights_is_a_valid_page(self):
        assert export_highlights(build_rm({"highlights": []})) == []

    def test_rectangles_are_required(self):
        data = {"highlights": [dict(ONE_HIGHLIGHT["highlights"][0], rectangles=[])]}
        with pytest.raises(ValueError, match="no rectangles"):
            build_rm(data)

    def test_color_accepts_numeric_value(self):
        data = {"highlights": [dict(ONE_HIGHLIGHT["highlights"][0], color=3)]}
        assert export_highlights(build_rm(data))[0]["color"] == "YELLOW"

    def test_color_rgba_survives(self):
        data = {
            "highlights": [
                dict(ONE_HIGHLIGHT["highlights"][0], color_rgba=[255, 229, 54, 255])
            ]
        }
        assert export_highlights(build_rm(data))[0]["color_rgba"] == [
            255,
            229,
            54,
            255,
        ]


class TestRoundTrip:
    def test_export_then_import_is_lossless(self):
        original = scene_to_dict(read_tree(open(HIGHLIGHTED, "rb")))
        assert export_highlights(build_rm(original)) == original["highlights"]


class TestMergeOntoBase:
    """Appending to a page that already has content, which must be preserved."""

    def test_existing_highlights_are_kept(self):
        merged = export_highlights(build_rm(ONE_HIGHLIGHT, base_path=HIGHLIGHTED))
        assert len(merged) == 5
        assert merged[0]["text"] == "The reMarkable uses electronic paper"
        assert merged[-1]["text"] == "appended highlight"

    def test_new_item_id_does_not_collide(self):
        merged = build_rm(ONE_HIGHLIGHT, base_path=HIGHLIGHTED)
        item_ids = [
            block.item.item_id
            for block in read_blocks(io.BytesIO(merged))
            if isinstance(block, SceneItemBlock)
        ]
        assert len(item_ids) == len(set(item_ids))

    def test_new_item_is_appended_to_the_chain(self):
        items = glyph_items(build_rm(ONE_HIGHLIGHT, base_path=HIGHLIGHTED))
        assert items[-1].left_id == items[-2].item_id
        assert items[-1].right_id == CrdtId(0, 0)

    def test_new_item_joins_the_existing_layer(self):
        items = glyph_items(build_rm(ONE_HIGHLIGHT, base_path=HIGHLIGHTED))
        blocks = [
            block
            for block in read_blocks(io.BytesIO(build_rm(ONE_HIGHLIGHT, base_path=HIGHLIGHTED)))
            if isinstance(block, SceneGlyphItemBlock)
        ]
        assert len({block.parent_id for block in blocks}) == 1
        assert items[-1].value.text == "appended highlight"

    def test_merging_nothing_leaves_the_page_untouched(self):
        with open(HIGHLIGHTED, "rb") as base:
            blocks = json_to_blocks({"highlights": []}, base_blocks=list(read_blocks(base)))
        assert len([b for b in blocks if isinstance(b, SceneGlyphItemBlock)]) == 4

    def test_removal_marks_the_item_deleted_rather_than_dropping_it(self):
        """The page is a CRDT: an item that just vanishes can be reinstated."""
        original = export_highlights(open(HIGHLIGHTED, "rb").read())
        data = {"highlights": [], "remove_highlights": [{"text": original[0]["text"]}]}
        with open(HIGHLIGHTED, "rb") as base:
            blocks = json_to_blocks(data, base_blocks=list(read_blocks(base)))

        glyphs = [b for b in blocks if isinstance(b, SceneGlyphItemBlock)]
        assert len(glyphs) == 4, "the block itself must survive"
        deleted = [b for b in glyphs if b.item.value is None]
        assert len(deleted) == 1
        assert deleted[0].item.deleted_length > 0

    def test_removed_highlight_is_gone_from_the_rebuilt_page(self):
        original = export_highlights(open(HIGHLIGHTED, "rb").read())
        data = {"highlights": [], "remove_highlights": [{"text": original[0]["text"]}]}
        remaining = export_highlights(build_rm(data, base_path=HIGHLIGHTED))
        assert len(remaining) == 3
        assert original[0]["text"] not in [h["text"] for h in remaining]

    def test_removal_keeps_the_item_id_so_the_chain_survives(self):
        original = export_highlights(open(HIGHLIGHTED, "rb").read())
        before = [i.item_id for i in glyph_items(open(HIGHLIGHTED, "rb").read())]
        data = {"highlights": [], "remove_highlights": [{"text": original[0]["text"]}]}
        with open(HIGHLIGHTED, "rb") as base:
            blocks = json_to_blocks(data, base_blocks=list(read_blocks(base)))
        after = [
            b.item.item_id for b in blocks if isinstance(b, SceneGlyphItemBlock)
        ]
        assert after == before

    def test_removal_and_addition_in_one_pass(self):
        original = export_highlights(open(HIGHLIGHTED, "rb").read())
        data = dict(
            ONE_HIGHLIGHT, remove_highlights=[{"text": original[0]["text"]}]
        )
        remaining = export_highlights(build_rm(data, base_path=HIGHLIGHTED))
        texts = [h["text"] for h in remaining]
        assert original[0]["text"] not in texts
        assert "appended highlight" in texts

    def test_unmatched_removal_changes_nothing(self):
        data = {"highlights": [], "remove_highlights": [{"text": "never highlighted"}]}
        assert len(export_highlights(build_rm(data, base_path=HIGHLIGHTED))) == 4

    def test_removal_matches_across_collapsed_whitespace(self):
        """A wrapped highlight is joined differently by the two sides."""
        original = export_highlights(open(HIGHLIGHTED, "rb").read())
        spaced = original[0]["text"].replace(" ", "  \n ")
        data = {"highlights": [], "remove_highlights": [{"text": spaced}]}
        assert len(export_highlights(build_rm(data, base_path=HIGHLIGHTED))) == 3

    def test_rectangles_disambiguate_two_copies_of_one_sentence(self):
        twice = {
            "highlights": [
                dict(ONE_HIGHLIGHT["highlights"][0], text="same"),
                dict(
                    ONE_HIGHLIGHT["highlights"][0],
                    text="same",
                    rectangles=[{"x": 100.0, "y": 100.0, "w": 50.0, "h": 20.0}],
                ),
            ]
        }
        page = build_rm(twice)
        data = {
            "highlights": [],
            "remove_highlights": [
                {"text": "same", "rectangles": [{"x": 100.0, "y": 100.0, "w": 5.0, "h": 5.0}]}
            ],
        }
        with open("test-two-copies.rm", "wb") as f:
            f.write(page)
        try:
            remaining = export_highlights(build_rm(data, base_path="test-two-copies.rm"))
        finally:
            Path("test-two-copies.rm").unlink()
        assert len(remaining) == 1
        assert remaining[0]["rectangles"][0]["x"] == -800.0

    def test_one_removal_takes_one_copy(self):
        data = {"highlights": [], "remove_highlights": [{"text": "same"}]}
        page = build_rm(
            {
                "highlights": [
                    dict(ONE_HIGHLIGHT["highlights"][0], text="same"),
                    dict(ONE_HIGHLIGHT["highlights"][0], text="same"),
                ]
            }
        )
        with open("test-dupes.rm", "wb") as f:
            f.write(page)
        try:
            remaining = export_highlights(build_rm(data, base_path="test-dupes.rm"))
        finally:
            Path("test-dupes.rm").unlink()
        assert len(remaining) == 1

    def test_result_counts_only_removals_that_matched(self):
        original = export_highlights(open(HIGHLIGHTED, "rb").read())
        data = {
            "highlights": [],
            "remove_highlights": [
                {"text": original[0]["text"]},
                {"text": "never highlighted"},
            ],
        }
        out = io.BytesIO()
        with open(HIGHLIGHTED, "rb") as base:
            result = json_to_rm(data, out, base=base)
        assert result.removals_requested == 2
        assert result.removed == 1

    def test_result_reports_nothing_removed_when_no_removals_asked(self):
        out = io.BytesIO()
        with open(HIGHLIGHTED, "rb") as base:
            result = json_to_rm(ONE_HIGHLIGHT, out, base=base)
        assert result.removals_requested == 0
        assert result.removed == 0
        assert result.added == 1

    def test_removal_on_a_fresh_page_is_a_no_op(self):
        data = dict(ONE_HIGHLIGHT, remove_highlights=[{"text": "appended highlight"}])
        assert len(export_highlights(build_rm(data))) == 1

    def test_merges_onto_a_page_with_strokes(self):
        merged = build_rm(ONE_HIGHLIGHT, base_path=RM_DIR / "abcd.strokes.rm")
        assert export_highlights(merged)[0]["text"] == "appended highlight"
        assert any(
            isinstance(item.value, si.Line)
            for item in (
                block.item
                for block in read_blocks(io.BytesIO(merged))
                if isinstance(block, SceneItemBlock)
            )
        )
