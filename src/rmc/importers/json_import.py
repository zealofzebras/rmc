"""Build rm files from the JSON structure produced by :mod:`rmc.exporters.json_export`.

Only text highlights (``GlyphRange`` scene items) are built. Strokes are not:
a stroke carries per-point speed/direction/pressure that no external producer
has, whereas a highlight is fully described by its text, colour and rectangles.

Two modes are supported:

* **from scratch** -- synthesize a minimal page containing one layer and the
  given highlights. Used for a PDF page that carries no annotations yet.
* **onto a base page** (``base_blocks``) -- keep every existing block and append
  the new highlights to the first layer. Used for a page that already has
  handwriting or highlights on it, which must not be destroyed.

Highlights already on a base page can also be removed, via ``remove_highlights``.
A removal is not a dropped block: the page is a CRDT, so an item that simply
vanishes from the file is an item the device is free to reinstate from its own
copy. It is instead recorded the way the device records its own deletions --
the block keeps its type and its ids, loses its value, and carries a non-zero
``deleted_length``.
"""

from __future__ import annotations

import dataclasses
import typing as tp
from uuid import UUID, uuid4

from rmscene import (
    AuthorIdsBlock,
    Block,
    CrdtId,
    CrdtSequence,
    CrdtSequenceItem,
    MigrationInfoBlock,
    PageInfoBlock,
    SceneGlyphItemBlock,
    SceneGroupItemBlock,
    SceneItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
    read_blocks,
    write_blocks,
)
from rmscene import scene_items as si
from rmscene.tagged_block_common import LwwValue

# The ids reMarkable itself uses for a fresh single-layer page. Reusing them
# keeps a generated page byte-comparable with a device-authored one.
ROOT_GROUP_ID = CrdtId(0, 1)
LAYER_GROUP_ID = CrdtId(0, 11)
LAYER_LABEL_ID = CrdtId(0, 12)
LAYER_ITEM_ID = CrdtId(0, 13)
END_MARKER = CrdtId(0, 0)

DEFAULT_LAYER_LABEL = "Layer 1"


@dataclasses.dataclass
class ImportResult:
    """What the import actually did, as opposed to what it was asked to do.

    `removed` counts the highlights that were found and marked deleted, which is
    not necessarily every entry in `remove_highlights`: one naming a highlight
    that is not on the page matches nothing. The caller has to be able to tell
    the two apart, because "the highlight is gone" and "I could not find it" lead
    to opposite conclusions about what the page now holds.
    """

    added: int
    removed: int
    removals_requested: int


def json_to_rm(
    data: dict,
    fout: tp.BinaryIO,
    base: tp.Optional[tp.BinaryIO] = None,
    author_uuid: tp.Optional[UUID] = None,
) -> ImportResult:
    """Write the highlights in `data` to `fout` as an rm file.

    When `base` is given its contents are preserved, the highlights are
    appended to it, and any `remove_highlights` entries matching an existing
    highlight on it are marked deleted.
    """
    base_blocks = list(read_blocks(base)) if base is not None else None
    blocks = json_to_blocks(data, base_blocks=base_blocks, author_uuid=author_uuid)
    write_blocks(fout, blocks, options=_write_options(base_blocks))
    return ImportResult(
        added=len(data.get("highlights") or []),
        removed=_count_new_tombstones(base_blocks, blocks),
        removals_requested=len(data.get("remove_highlights") or []),
    )


def _count_new_tombstones(
    base_blocks: tp.Optional[list[Block]], blocks: list[Block]
) -> int:
    """How many glyph items this import turned into tombstones.

    Counted by comparing the two block lists rather than tallied while removing,
    so the number can only ever describe what is really in the output.
    """
    if not base_blocks:
        return 0
    was_live = {
        block.item.item_id
        for block in base_blocks
        if isinstance(block, SceneGlyphItemBlock) and block.item.value is not None
    }
    return sum(
        1
        for block in blocks
        if isinstance(block, SceneGlyphItemBlock)
        and block.item.value is None
        and block.item.item_id in was_live
    )


def _write_options(base_blocks: tp.Optional[list[Block]]) -> tp.Optional[dict]:
    """Return the writer options needed to reproduce the base page's format.

    Points are stored as floats in the pre-3.0 line format and as integers
    after it, and the writer picks between them from the file version alone.
    Writing an old page back out with the default (newest) version therefore
    fails on its own strokes, so the version has to be recovered from the
    points that were read.
    """
    if not base_blocks:
        return None
    for block in base_blocks:
        if not isinstance(block, SceneLineItemBlock):
            continue
        line = block.item.value
        if not isinstance(line, si.Line):
            continue
        for point in line.points:
            return None if isinstance(point.speed, int) else {"version": "3.0"}
    return None


def json_to_blocks(
    data: dict,
    base_blocks: tp.Optional[list[Block]] = None,
    author_uuid: tp.Optional[UUID] = None,
) -> list[Block]:
    """Return the blocks representing `data`, optionally merged onto `base_blocks`."""
    highlights = [_glyph_range_from_dict(h) for h in data.get("highlights") or []]
    removals = [_removal_from_dict(r) for r in data.get("remove_highlights") or []]

    if base_blocks is None:
        # A page built from scratch has nothing on it to remove.
        return _new_page_blocks(highlights, author_uuid)
    return _merge_into_page_blocks(base_blocks, highlights, removals)


# ---------------------------------------------------------------------------
# Reading the JSON form
# ---------------------------------------------------------------------------


def _glyph_range_from_dict(raw: dict) -> si.GlyphRange:
    text = raw.get("text") or ""
    rectangles = [_rectangle_from_dict(r) for r in raw.get("rectangles") or []]
    if not rectangles:
        raise ValueError(f"highlight {text!r} has no rectangles; it could not be placed")

    color_rgba = raw.get("color_rgba")
    return si.GlyphRange(
        # `start` is only meaningful in the pre-3.6 block format, and a producer
        # that is not the device cannot know the PDF glyph offset. Passing it
        # through when present keeps a round-trip lossless.
        start=raw.get("start"),
        length=raw.get("length", len(text)),
        text=text,
        color=_pen_color_from_json(raw.get("color")),
        rectangles=rectangles,
        color_rgba=tuple(color_rgba) if color_rgba is not None else None,
    )


@dataclasses.dataclass
class _Removal:
    """One highlight to mark deleted, as named by the caller.

    The caller identifies it by what it can see -- the text and where it sits --
    rather than by CRDT id, because the id never leaves the `rm` file and no
    producer outside this module has one to quote back.
    """

    text: str
    rectangles: list

    def matches(self, glyph: si.GlyphRange) -> bool:
        if _normalize_text(glyph.text) != self.text:
            return False
        # Text alone is ambiguous: the same sentence can be highlighted twice on
        # one page. Position disambiguates when the caller supplies it, and is
        # skipped when it does not rather than refusing to remove anything.
        if not self.rectangles:
            return True
        return any(
            _rectangles_intersect(mine, theirs)
            for mine in self.rectangles
            for theirs in glyph.rectangles or []
        )


def _removal_from_dict(raw: dict) -> _Removal:
    return _Removal(
        text=_normalize_text(raw.get("text") or ""),
        rectangles=[_rectangle_from_dict(r) for r in raw.get("rectangles") or []],
    )


def _normalize_text(text: str) -> str:
    """Collapse whitespace, which a wrapped highlight disagrees about.

    The text is otherwise compared exactly: a removal is named from the very
    page it is removed from, so anything looser risks taking a neighbour.
    """
    return " ".join(text.split())


def _rectangles_intersect(one: si.Rectangle, other: si.Rectangle) -> bool:
    return (
        one.x < other.x + other.w
        and other.x < one.x + one.w
        and one.y < other.y + other.h
        and other.y < one.y + one.h
    )


def _rectangle_from_dict(raw: dict) -> si.Rectangle:
    return si.Rectangle(
        x=float(raw["x"]), y=float(raw["y"]), w=float(raw["w"]), h=float(raw["h"])
    )


def _pen_color_from_json(value) -> si.PenColor:
    """Accept either a `PenColor` name ("YELLOW") or its numeric value."""
    if value is None:
        return si.PenColor.YELLOW
    if isinstance(value, si.PenColor):
        return value
    if isinstance(value, int):
        return si.PenColor(value)
    return si.PenColor[str(value)]


# ---------------------------------------------------------------------------
# Building a page from scratch
# ---------------------------------------------------------------------------


def _new_page_blocks(
    highlights: list[si.GlyphRange], author_uuid: tp.Optional[UUID]
) -> list[Block]:
    if author_uuid is None:
        author_uuid = uuid4()
    author_id = 1

    blocks: list[Block] = [
        AuthorIdsBlock(author_uuids={author_id: author_uuid}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(
            loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0
        ),
        SceneTreeBlock(
            tree_id=LAYER_GROUP_ID,
            node_id=END_MARKER,
            is_update=True,
            parent_id=ROOT_GROUP_ID,
        ),
        TreeNodeBlock(si.Group(node_id=ROOT_GROUP_ID)),
        TreeNodeBlock(
            si.Group(
                node_id=LAYER_GROUP_ID,
                label=LwwValue(timestamp=LAYER_LABEL_ID, value=DEFAULT_LAYER_LABEL),
            )
        ),
        SceneGroupItemBlock(
            parent_id=ROOT_GROUP_ID,
            item=CrdtSequenceItem(
                item_id=LAYER_ITEM_ID,
                left_id=END_MARKER,
                right_id=END_MARKER,
                deleted_length=0,
                value=LAYER_GROUP_ID,
            ),
        ),
    ]

    blocks.extend(
        _glyph_blocks(
            highlights,
            parent_id=LAYER_GROUP_ID,
            author_id=author_id,
            first_counter=LAYER_ITEM_ID.part2 + 1,
            left_id=END_MARKER,
        )
    )
    return blocks


# ---------------------------------------------------------------------------
# Merging into an existing page
# ---------------------------------------------------------------------------


def _merge_into_page_blocks(
    base_blocks: list[Block],
    highlights: list[si.GlyphRange],
    removals: tp.Optional[list[_Removal]] = None,
) -> list[Block]:
    blocks = _apply_removals(list(base_blocks), removals or [])
    if not highlights:
        return blocks

    layer_id = _find_layer_id(blocks)
    if layer_id is None:
        raise ValueError("base rm file has no layer to append highlights to")

    return blocks + _glyph_blocks(
        highlights,
        parent_id=layer_id,
        author_id=_pick_author_id(blocks),
        # The item counter is shared across authors, so a new id has to clear
        # every id already in the file, structural ones included.
        first_counter=_max_crdt_counter(blocks) + 1,
        left_id=_last_item_id(blocks, layer_id),
    )


def _apply_removals(blocks: list[Block], removals: list[_Removal]) -> list[Block]:
    """Mark every block naming a removed highlight as deleted, in place of it.

    The block is rewritten rather than dropped. Its ids are what the rest of the
    page's sequence links to, so removing it outright would orphan whatever was
    threaded after it, and would leave the device's copy of the item with
    nothing saying it is gone.
    """
    if not removals:
        return blocks

    pending = list(removals)
    result: list[Block] = []
    for block in blocks:
        glyph = _glyph_value(block)
        matched = next(
            (removal for removal in pending if removal.matches(glyph)), None
        ) if glyph is not None else None
        if matched is None:
            result.append(block)
            continue
        # One removal takes one highlight: a page holding the same sentence
        # twice keeps the copy that was not asked for.
        pending.remove(matched)
        result.append(_deleted_block(block))
    return result


def _glyph_value(block: Block) -> tp.Optional[si.GlyphRange]:
    if not isinstance(block, SceneGlyphItemBlock):
        return None
    value = block.item.value
    return value if isinstance(value, si.GlyphRange) else None


def _deleted_block(block: SceneGlyphItemBlock) -> SceneGlyphItemBlock:
    """The same block, in the form the device writes for a deleted item.

    `deleted_length` counts what the item covered; the writer omits the value
    subblock once the value is gone, which is exactly the shape a device-authored
    deletion has.
    """
    return dataclasses.replace(
        block,
        item=dataclasses.replace(block.item, value=None, deleted_length=1),
        extra_value_data=b"",
    )


def _find_layer_id(blocks: list[Block]) -> tp.Optional[CrdtId]:
    """Return the id of the first layer group, i.e. the first child of the root."""
    for block in blocks:
        if isinstance(block, SceneGroupItemBlock) and block.parent_id == ROOT_GROUP_ID:
            return block.item.value
    # A page whose root has no group child still has a tree; fall back to the
    # first SceneTreeBlock, which is the layer the device created.
    for block in blocks:
        if isinstance(block, SceneTreeBlock):
            return block.tree_id
    return None


def _pick_author_id(blocks: list[Block]) -> int:
    for block in blocks:
        if isinstance(block, AuthorIdsBlock) and block.author_uuids:
            return min(block.author_uuids)
    return 1


def _last_item_id(blocks: list[Block], layer_id: CrdtId) -> CrdtId:
    """Return the id of the last item in `layer_id`'s sequence.

    The sequence is a CRDT: order comes from the left/right links, not from the
    order the blocks happen to appear in. Walking the chain from the start
    marker gives the tail to append after.
    """
    items = [
        block.item
        for block in blocks
        if isinstance(block, SceneItemBlock) and block.parent_id == layer_id
    ]
    if not items:
        return END_MARKER

    by_left: dict[CrdtId, CrdtSequenceItem] = {item.left_id: item for item in items}
    current = END_MARKER
    seen: set[CrdtId] = set()
    while current in by_left:
        nxt = by_left[current].item_id
        if nxt in seen:
            # A malformed or concurrently-edited chain must not spin forever;
            # appending after the last id reached is still a valid sequence.
            break
        seen.add(nxt)
        current = nxt
    return current


def _glyph_blocks(
    highlights: list[si.GlyphRange],
    parent_id: CrdtId,
    author_id: int,
    first_counter: int,
    left_id: CrdtId,
) -> list[Block]:
    blocks: list[Block] = []
    for offset, highlight in enumerate(highlights):
        item_id = CrdtId(author_id, first_counter + offset)
        blocks.append(
            SceneGlyphItemBlock(
                parent_id=parent_id,
                item=CrdtSequenceItem(
                    item_id=item_id,
                    left_id=left_id,
                    right_id=END_MARKER,
                    deleted_length=0,
                    value=highlight,
                ),
            )
        )
        left_id = item_id
    return blocks


# ---------------------------------------------------------------------------
# Id allocation
# ---------------------------------------------------------------------------

def _max_crdt_counter(blocks: list[Block]) -> int:
    """Return the highest `part2` of any CrdtId anywhere in `blocks`."""
    highest = 0
    for block in blocks:
        for crdt_id in _walk_crdt_ids(block):
            highest = max(highest, crdt_id.part2)
    return highest


def _walk_crdt_ids(value, depth: int = 0) -> tp.Iterator[CrdtId]:
    # Scene trees are shallow; the bound only guards against a cyclic structure
    # turning id allocation into an infinite loop.
    if depth > 20:
        return
    if isinstance(value, CrdtId):
        yield value
        return
    if isinstance(value, CrdtSequence):
        for item in value.sequence_items():
            yield from _walk_crdt_ids(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_crdt_ids(key, depth + 1)
            yield from _walk_crdt_ids(item, depth + 1)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk_crdt_ids(item, depth + 1)
        return
    # Covers Block, Group, Text, GlyphRange, LwwValue, CrdtSequenceItem, ...
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _walk_crdt_ids(getattr(value, field.name), depth + 1)
