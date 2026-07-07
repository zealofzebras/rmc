# JSON Export

`rmc` can export the full structured content of a reMarkable `.rm` file as JSON.
This includes all text paragraphs with their styles, every stroke on every layer,
and any highlighted text ranges.

## How to call

```bash
# Write JSON to stdout
rmc -t json file.rm

# Write JSON to a file (format auto-detected from .json extension)
rmc file.rm -o file.json

# Explicit output file with explicit format
rmc -t json file.rm -o file.json
```

Using the Python API directly:

```python
from rmscene import read_tree
from rmc.exporters.json_export import tree_to_json, scene_to_dict

# Write JSON to a file object
with open("file.rm", "rb") as f:
    tree = read_tree(f)

with open("file.json", "w") as out:
    tree_to_json(tree, out)

# Or get a plain Python dict
with open("file.rm", "rb") as f:
    tree = read_tree(f)

data = scene_to_dict(tree)
```

## Output structure

The top-level object always contains three keys: `text`, `layers`, and
`highlights`.

```json
{
  "text": [...],
  "layers": [...],
  "highlights": [...]
}
```

### `text` — typed paragraphs

An array of paragraph objects, each with:

| Field     | Type   | Description                                      |
|-----------|--------|--------------------------------------------------|
| `style`   | string | Paragraph style (see below)                      |
| `content` | string | Plain-text content of the paragraph              |

Paragraph styles correspond to the text formatting options on the reMarkable:

| `style` value      | reMarkable formatting          |
|--------------------|-------------------------------|
| `PLAIN`            | Normal body text              |
| `BOLD`             | Bold / medium heading         |
| `HEADING`          | Large heading                 |
| `BULLET`           | Bullet list item (`•`)        |
| `BULLET2`          | Indented bullet (`◦`)         |
| `CHECKBOX`         | Unchecked checkbox            |
| `CHECKBOX_CHECKED` | Checked checkbox              |

Example — a page with heading, bullet and body text
(`tests/rm/Bold_Heading_Bullet_Normal.rm`):

```json
{
  "text": [
    { "style": "BOLD",    "content": "A" },
    { "style": "HEADING", "content": "new line" },
    { "style": "BULLET",  "content": "B is a letter of the alphabet" },
    { "style": "PLAIN",   "content": "C" }
  ],
  "layers": [
    {
      "id": "0:11",
      "label": "Layer 1",
      "visible": true,
      "strokes": [],
      "groups": []
    }
  ],
  "highlights": []
}
```

### `layers` — stroke data

An array of layer objects. Each layer corresponds to a top-level group in the
reMarkable scene tree (usually a named layer in the reMarkable app).

**Layer object**

| Field     | Type    | Description                                      |
|-----------|---------|--------------------------------------------------|
| `id`      | string  | CRDT node ID in `"part1:part2"` format           |
| `label`   | string  | Layer name (may be `null` or `""`)               |
| `visible` | boolean | Whether the layer is currently visible           |
| `strokes` | array   | Direct stroke children (see below)               |
| `groups`  | array   | Nested group children (same structure as layer)  |

Strokes are normally found inside nested `groups` (one group per drawn stroke
cluster) rather than directly on the layer.

**Stroke object**

| Field             | Type           | Description                                   |
|-------------------|----------------|-----------------------------------------------|
| `tool`            | string         | Pen type name (e.g. `"FINELINER_2"`)          |
| `color`           | string         | Named colour (e.g. `"BLACK"`, `"BLUE"`)       |
| `color_rgba`      | array or null  | `[R, G, B, A]` (0–255); `null` for older files |
| `thickness_scale` | number         | Pen size multiplier                           |
| `points`          | array          | Ordered list of point objects (see below)     |

**Point object**

| Field       | Type   | Description                                        |
|-------------|--------|----------------------------------------------------|
| `x`         | number | X position (screen units, origin at page centre)  |
| `y`         | number | Y position (screen units, origin at top)           |
| `speed`     | number | Pen speed at this sample                           |
| `direction` | number | Pen tilt/direction                                 |
| `width`     | number | Pen width at this sample                           |
| `pressure`  | number | Pen pressure at this sample                        |

Example — a page with hand-drawn strokes
(trimmed to 2 points each, from `tests/rm/abcd.strokes.rm`):

```json
{
  "text": [],
  "layers": [
    {
      "id": "0:11",
      "label": "Layer 1",
      "visible": true,
      "strokes": [
        {
          "tool": "MECHANICAL_PENCIL_2",
          "color": "BLACK",
          "color_rgba": null,
          "thickness_scale": 2.0,
          "points": [
            {
              "x": -437.427,
              "y": 244.635,
              "speed": 0.540,
              "direction": 127.5,
              "width": 24,
              "pressure": 178.5
            },
            {
              "x": -434.830,
              "y": 239.814,
              "speed": 2.726,
              "direction": 202.050,
              "width": 24,
              "pressure": 178.553
            }
          ]
        }
      ],
      "groups": []
    }
  ],
  "highlights": []
}
```

Example — mixed text and strokes (from `tests/rm/text_and_strokes.rm`):

```json
{
  "text": [
    { "style": "PLAIN", "content": "abed" },
    { "style": "PLAIN", "content": "" }
  ],
  "layers": [
    {
      "id": "0:11",
      "label": "Layer 1",
      "visible": true,
      "strokes": [],
      "groups": [
        {
          "id": "1:20",
          "label": "",
          "visible": true,
          "strokes": [
            {
              "tool": "MECHANICAL_PENCIL_2",
              "color": "BLACK",
              "color_rgba": null,
              "thickness_scale": 2.0,
              "points": [
                {
                  "x": 2.165,
                  "y": 246.494,
                  "speed": 0.484,
                  "direction": 62.397,
                  "width": 24,
                  "pressure": 178.5
                }
              ]
            }
          ],
          "groups": []
        }
      ]
    }
  ],
  "highlights": []
}
```

### `highlights` — highlighted text ranges

An array of highlight objects. Each object represents a continuous highlighted
span within the root text.

| Field    | Type   | Description                                   |
|----------|--------|-----------------------------------------------|
| `text`   | string | The highlighted text                          |
| `start`  | number | Character offset within the full text         |
| `length` | number | Number of characters in the highlighted span  |

Example:

```json
{
  "highlights": [
    { "text": "B is a letter", "start": 12, "length": 13 }
  ]
}
```
