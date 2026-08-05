"""Export rm file content as JSON."""

import json
import typing as tp

from rmscene import SceneTree, read_tree
from rmscene import scene_items as si
from rmscene.text import TextDocument


def rm_to_json(rm_path, json_path):
    """Convert `rm_path` to JSON at `json_path`."""
    with open(rm_path, "rb") as infile, open(json_path, "wt") as outfile:
        tree = read_tree(infile)
        tree_to_json(tree, outfile)


def tree_to_json(tree: SceneTree, fout):
    """Convert a SceneTree to JSON and write to fout."""
    result = scene_to_dict(tree)
    json.dump(result, fout, indent=2)
    fout.write("\n")


def scene_to_dict(tree: SceneTree) -> dict:
    """Convert a SceneTree to a JSON-serializable dict.

    The returned dict has the following structure::

        {
          "text": [
            {"style": "HEADING", "content": "Title"},
            {"style": "PLAIN", "content": "Body text"},
            {"style": "BULLET", "content": "A bullet point"}
          ],
          "layers": [
            {
              "id": "0:11",
              "label": "Layer 1",
              "visible": true,
              "strokes": [
                {
                  "tool": "FINELINER_2",
                  "color": "BLACK",
                  "color_rgba": [0, 0, 0, 255],
                  "thickness_scale": 1.0,
                  "points": [
                    {
                      "x": 622.5,
                      "y": 321.75,
                      "speed": 51,
                      "direction": 154,
                      "width": 4,
                      "pressure": 112
                    }
                  ]
                }
              ],
              "groups": []
            }
          ],
          "highlights": [
            {
              "text": "highlighted text",
              "start": 5,
              "length": 16,
              "color": "YELLOW",
              "color_rgba": null,
              "rectangles": [
                {"x": -810.11, "y": 663.87, "w": 669.95, "h": 56.3}
              ]
            }
          ],
          "paper_size": [1404, 1872]
        }

    `rectangles` are in the same screen-unit coordinate space as stroke points,
    so a consumer can project highlights onto a page with the same transform it
    already uses for handwriting.

    `paper_size` is the size of that coordinate space -- the device's own
    canvas, not the size of any PDF behind it. It is what tells a consumer how
    many screen units wide a page is, which differs between devices, and it is
    ``null`` for a file written before the device recorded it.
    """
    result: dict = {}

    result["text"] = _text_to_list(tree.root_text) if tree.root_text is not None else []
    result["layers"] = _group_to_layers(tree.root)
    result["highlights"] = _collect_highlights(tree)
    result["paper_size"] = _paper_size(tree.scene_info)

    return result


def _paper_size(scene_info) -> tp.Optional[list]:
    """Return the canvas size as ``[width, height]``, or None if unrecorded.

    Firmware 3.27 added two more precise spellings of the same value alongside
    the original integer pair. The LWW one is preferred because it is the one
    the device updates.
    """
    if scene_info is None:
        return None
    for size in (
        getattr(scene_info, "paper_size_lww", None),
        getattr(scene_info, "paper_size_raw", None),
        scene_info.paper_size,
    ):
        value = getattr(size, "value", size)
        if value is not None:
            return list(value)
    return None


def _text_to_list(root_text: si.Text) -> list:
    doc = TextDocument.from_scene_item(root_text)
    return [
        {
            "style": p.style.value.name,
            "content": str(p),
        }
        for p in doc.contents
    ]


def _group_to_layers(root: si.Group) -> list:
    """Return the top-level layer groups from the root group."""
    layers = []
    for child in root.children.values():
        if child is None:
            continue
        if isinstance(child, si.Group):
            layers.append(_group_to_dict(child))
    return layers


def _group_to_dict(group: si.Group) -> dict:
    strokes = []
    subgroups = []
    for child in group.children.values():
        if child is None:
            continue
        if isinstance(child, si.Line):
            strokes.append(_line_to_dict(child))
        elif isinstance(child, si.Group):
            subgroups.append(_group_to_dict(child))

    node_id = group.node_id
    return {
        "id": f"{node_id.part1}:{node_id.part2}",
        "label": group.label.value if group.label is not None else None,
        "visible": group.visible.value if group.visible is not None else True,
        "strokes": strokes,
        "groups": subgroups,
    }


def _line_to_dict(line: si.Line) -> dict:
    color_rgba_raw = getattr(line, "color_rgba", None)
    color_rgba = list(color_rgba_raw) if color_rgba_raw is not None else None
    return {
        "tool": line.tool.name,
        "color": line.color.name,
        "color_rgba": color_rgba,
        "thickness_scale": line.thickness_scale,
        "points": [_point_to_dict(p) for p in line.points],
    }


def _point_to_dict(point) -> dict:
    return {
        "x": point.x,
        "y": point.y,
        "speed": point.speed,
        "direction": point.direction,
        "width": point.width,
        "pressure": point.pressure,
    }


def _collect_highlights(tree: SceneTree) -> list:
    highlights = []
    for item in tree.walk():
        if isinstance(item, si.GlyphRange):
            highlights.append(_glyph_range_to_dict(item))
    return highlights


def _glyph_range_to_dict(item: si.GlyphRange) -> dict:
    color_rgba_raw = getattr(item, "color_rgba", None)
    return {
        "text": item.text,
        # Absent since reMarkable 3.6; kept so a consumer can still tie the
        # highlight back to a position in the PDF's text stream when it is there.
        "start": item.start,
        # The stored length, which is not always `len(text)` -- see
        # `glyph_range_from_stream`.
        "length": item.length,
        "color": item.color.name,
        "color_rgba": list(color_rgba_raw) if color_rgba_raw is not None else None,
        "rectangles": [_rectangle_to_dict(r) for r in item.rectangles],
    }


def _rectangle_to_dict(rect: si.Rectangle) -> dict:
    return {"x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h}
