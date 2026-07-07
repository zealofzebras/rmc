"""Tests for the JSON export functionality."""

import io
import json
from pathlib import Path

import pytest

from rmc.exporters.json_export import scene_to_dict, tree_to_json

try:
    from rmscene import read_tree
except ImportError:
    pytest.skip("rmscene not installed", allow_module_level=True)

RM_DIR = Path(__file__).parent / "rm"


def read_rm(filename: str):
    with open(RM_DIR / filename, "rb") as f:
        return read_tree(f)


def export_json(filename: str) -> dict:
    tree = read_rm(filename)
    return scene_to_dict(tree)


class TestJsonStructure:
    """Verify that the JSON output has the expected top-level structure."""

    def test_top_level_keys(self):
        result = export_json("abcd.strokes.rm")
        assert set(result.keys()) == {"text", "layers", "highlights"}

    def test_text_is_list(self):
        result = export_json("abcd.strokes.rm")
        assert isinstance(result["text"], list)

    def test_layers_is_list(self):
        result = export_json("abcd.strokes.rm")
        assert isinstance(result["layers"], list)

    def test_highlights_is_list(self):
        result = export_json("abcd.strokes.rm")
        assert isinstance(result["highlights"], list)


class TestTextExport:
    """Verify text/paragraph export."""

    def test_text_content(self):
        result = export_json("abcd.text.rm")
        texts = [p["content"] for p in result["text"]]
        assert "abc" in texts

    def test_text_paragraph_keys(self):
        result = export_json("abcd.text.rm")
        assert len(result["text"]) > 0
        para = result["text"][0]
        assert set(para.keys()) == {"style", "content"}

    def test_text_styles_heading(self):
        result = export_json("Bold_Heading_Bullet_Normal.rm")
        styles = {p["style"] for p in result["text"]}
        assert "HEADING" in styles

    def test_text_styles_bold(self):
        result = export_json("Bold_Heading_Bullet_Normal.rm")
        styles = {p["style"] for p in result["text"]}
        assert "BOLD" in styles

    def test_text_styles_bullet(self):
        result = export_json("Bold_Heading_Bullet_Normal.rm")
        styles = {p["style"] for p in result["text"]}
        assert "BULLET" in styles

    def test_text_styles_plain(self):
        result = export_json("Bold_Heading_Bullet_Normal.rm")
        styles = {p["style"] for p in result["text"]}
        assert "PLAIN" in styles

    def test_text_content_values(self):
        result = export_json("Bold_Heading_Bullet_Normal.rm")
        content_map = {p["style"]: p["content"] for p in result["text"]}
        assert content_map["BOLD"] == "A"
        assert content_map["HEADING"] == "new line"
        assert content_map["BULLET"] == "B is a letter of the alphabet"
        assert content_map["PLAIN"] == "C"

    def test_no_text_gives_empty_list(self):
        result = export_json("abcd.strokes.rm")
        assert result["text"] == []

    def test_checkbox_styles(self):
        result = export_json("keyboard-checkboxes-and-bullets.rm")
        styles = {p["style"] for p in result["text"]}
        assert "CHECKBOX" in styles or "CHECKBOX_CHECKED" in styles


class TestLayerExport:
    """Verify layer and stroke export."""

    def test_at_least_one_layer(self):
        result = export_json("abcd.strokes.rm")
        assert len(result["layers"]) >= 1

    def test_layer_keys(self):
        result = export_json("abcd.strokes.rm")
        layer = result["layers"][0]
        assert set(layer.keys()) == {"id", "label", "visible", "strokes", "groups"}

    def test_layer_label(self):
        result = export_json("abcd.strokes.rm")
        assert result["layers"][0]["label"] == "Layer 1"

    def test_layer_visible_bool(self):
        result = export_json("abcd.strokes.rm")
        assert isinstance(result["layers"][0]["visible"], bool)
        assert result["layers"][0]["visible"] is True

    def test_layer_id_format(self):
        result = export_json("abcd.strokes.rm")
        layer_id = result["layers"][0]["id"]
        assert ":" in layer_id
        parts = layer_id.split(":")
        assert len(parts) == 2
        assert all(p.isdigit() for p in parts)

    def test_strokes_present(self):
        result = export_json("abcd.strokes.rm")
        all_strokes = []
        for layer in result["layers"]:
            all_strokes.extend(layer["strokes"])
            for g in layer["groups"]:
                all_strokes.extend(g["strokes"])
        assert len(all_strokes) > 0

    def test_stroke_keys(self):
        result = export_json("abcd.strokes.rm")
        stroke = _first_stroke(result)
        assert set(stroke.keys()) == {
            "tool", "color", "color_rgba", "thickness_scale", "points"
        }

    def test_stroke_tool_is_string(self):
        result = export_json("abcd.strokes.rm")
        stroke = _first_stroke(result)
        assert isinstance(stroke["tool"], str)

    def test_stroke_color_is_string(self):
        result = export_json("abcd.strokes.rm")
        stroke = _first_stroke(result)
        assert isinstance(stroke["color"], str)
        assert stroke["color"] == "BLACK"

    def test_stroke_thickness_scale_is_float(self):
        result = export_json("abcd.strokes.rm")
        stroke = _first_stroke(result)
        assert isinstance(stroke["thickness_scale"], float)

    def test_stroke_has_points(self):
        result = export_json("abcd.strokes.rm")
        stroke = _first_stroke(result)
        assert len(stroke["points"]) > 0

    def test_point_keys(self):
        result = export_json("abcd.strokes.rm")
        stroke = _first_stroke(result)
        point = stroke["points"][0]
        assert set(point.keys()) == {"x", "y", "speed", "direction", "width", "pressure"}

    def test_point_coordinates_are_numeric(self):
        result = export_json("abcd.strokes.rm")
        stroke = _first_stroke(result)
        point = stroke["points"][0]
        assert isinstance(point["x"], (int, float))
        assert isinstance(point["y"], (int, float))


class TestJsonSerialization:
    """Verify that the output is valid JSON."""

    def test_tree_to_json_valid_json(self):
        tree = read_rm("abcd.strokes.rm")
        buf = io.StringIO()
        tree_to_json(tree, buf)
        buf.seek(0)
        parsed = json.loads(buf.read())
        assert isinstance(parsed, dict)

    def test_tree_to_json_ends_with_newline(self):
        tree = read_rm("abcd.strokes.rm")
        buf = io.StringIO()
        tree_to_json(tree, buf)
        assert buf.getvalue().endswith("\n")

    def test_tree_to_json_is_indented(self):
        tree = read_rm("abcd.strokes.rm")
        buf = io.StringIO()
        tree_to_json(tree, buf)
        content = buf.getvalue()
        assert "\n  " in content

    def test_all_test_files_produce_valid_json(self):
        for rm_file in RM_DIR.glob("*.rm"):
            tree = read_rm(rm_file.name)
            buf = io.StringIO()
            tree_to_json(tree, buf)
            buf.seek(0)
            parsed = json.loads(buf.read())
            assert "text" in parsed, f"{rm_file.name} missing 'text'"
            assert "layers" in parsed, f"{rm_file.name} missing 'layers'"
            assert "highlights" in parsed, f"{rm_file.name} missing 'highlights'"

    def test_text_and_strokes_file(self):
        result = export_json("text_and_strokes.rm")
        assert len(result["text"]) > 0
        all_strokes = _collect_strokes(result)
        assert len(all_strokes) > 0

    def test_multiple_layers(self):
        result = export_json("Normal_A_stroke_2_layers.rm")
        assert len(result["layers"]) >= 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_stroke(result: dict) -> dict:
    """Return the first stroke found anywhere in the layer tree."""
    for layer in result["layers"]:
        if layer["strokes"]:
            return layer["strokes"][0]
        for g in layer["groups"]:
            if g["strokes"]:
                return g["strokes"][0]
    raise AssertionError("No strokes found in result")


def _collect_strokes(result: dict) -> list:
    strokes = []
    for layer in result["layers"]:
        strokes.extend(layer["strokes"])
        for g in layer["groups"]:
            strokes.extend(g["strokes"])
    return strokes
