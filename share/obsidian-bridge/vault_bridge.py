#!/usr/bin/env python3
"""
Obsidian JSON Canvas 1.0 (.canvas) & Local Markdown (.md) Vault Bridge
for Omalauncher OWL 22 (25-sith-command-deck.owl).

Zero external dependencies (Python 3 stdlib only).
All subcommands emit a single structured JSON object to stdout for direct
consumption by OWL `cmd(...) json` / `file(...) json` sources and `run(...)`.

Subcommands:
  import-canvas  Convert JSON Canvas 1.0 (.canvas) -> OWL deck JSON
  export-canvas  Convert OWL deck JSON -> JSON Canvas 1.0 (.canvas)
  scan-vault     Scan local Obsidian vault (.md) -> OWL deck JSON + backlinks
  export-md      Export OWL deck cards -> Obsidian vault (.md) with frontmatter
  sync           Bidirectional sync between Vault (.md/.canvas) and OWL deck JSON
  validate       Strict JSON Canvas 1.0 specification validator
  demo           Generate a sample Obsidian vault + .canvas for testing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants & Spec Tables (JSON Canvas 1.0 + 25-sith-command-deck.owl)
# ---------------------------------------------------------------------------

SPEC_VERSION = "1.0"
BRIDGE_STATE_FILE = ".sith-bridge-state.json"

VALID_NODE_TYPES = frozenset({"text", "file", "link", "group"})
VALID_SIDES = frozenset({"top", "right", "bottom", "left"})
VALID_ENDS = frozenset({"none", "arrow"})
VALID_BG_STYLES = frozenset({"cover", "ratio", "repeat"})
VALID_PRESET_COLORS = frozenset({"1", "2", "3", "4", "5", "6"})
IGNORED_VAULT_DIRS = frozenset({
    ".obsidian", ".trash", ".git", "node_modules", ".stversions", "__pycache__"
})

HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]]+)\]\]")
TAG_RE = re.compile(r"(?:^|(?<=[\s(\[]))#([A-Za-z][A-Za-z0-9_/\-]*)")
TASK_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$", re.MULTILINE)
FENCED_CODE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

# 25-sith-command-deck.owl `notePalettes` (indices 0..5)
SITH_PALETTES: list[dict[str, Any]] = [
    {"idx": 0, "id": "crimson",  "hex": "#ff1738", "preset": "1"},
    {"idx": 1, "id": "obsidian", "hex": "#6b081b", "preset": "5"},
    {"idx": 2, "id": "blood",    "hex": "#cc0022", "preset": "1"},
    {"idx": 3, "id": "void",     "hex": "#a82eff", "preset": "6"},
    {"idx": 4, "id": "toxic",    "hex": "#00ff7f", "preset": "4"},
    {"idx": 5, "id": "solar",    "hex": "#ff6a00", "preset": "2"},
]

PRESET_TO_PALETTE_IDX: dict[str, int] = {
    "1": 0,  # Red    -> crimson
    "2": 5,  # Orange -> solar
    "3": 5,  # Yellow -> solar
    "4": 4,  # Green  -> toxic
    "5": 1,  # Cyan   -> obsidian
    "6": 3,  # Purple -> void
}

PALETTE_IDX_TO_PRESET: dict[int, str] = {
    0: "1",  # crimson  -> Red
    1: "5",  # obsidian -> Cyan
    2: "1",  # blood    -> Red
    3: "6",  # void     -> Purple
    4: "4",  # toxic    -> Green
    5: "2",  # solar    -> Orange
}

# ---------------------------------------------------------------------------
# Utility Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def deterministic_canvas_id(seed: str) -> str:
    """Obsidian-compatible 16-char lowercase hex ID."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, content: str) -> None:
    """Write atomically via sibling temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def safe_vault_path(vault_root: Path, rel_path: str) -> Path:
    """Resolve a vault-relative path safely inside vault_root (prevents traversal)."""
    root = vault_root.resolve()
    cleaned = rel_path.replace("\\", "/").lstrip("/")
    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes vault root: {rel_path!r}") from exc
    return candidate


def slugify_filename(title: str, fallback: str = "Untitled") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:96] if cleaned else fallback


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def canvas_color_to_palette(color: str | None) -> tuple[int, str]:
    """Map JSON Canvas `canvasColor` -> (notePalettes index, preserved hex or '')."""
    if not color or not isinstance(color, str):
        return 0, ""
    c = color.strip()
    if c in PRESET_TO_PALETTE_IDX:
        return PRESET_TO_PALETTE_IDX[c], ""
    if HEX_COLOR_RE.match(c):
        r, g, b = _hex_to_rgb(c)
        best_idx = 0
        best_dist = float("inf")
        for pal in SITH_PALETTES:
            pr, pg, pb = _hex_to_rgb(pal["hex"])
            dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
            if dist < best_dist:
                best_dist = dist
                best_idx = pal["idx"]
        return best_idx, c.lower()
    return 0, ""


def palette_to_canvas_color(color_idx: int, color_hex: str = "") -> str:
    """Map OWL notePalettes index / custom hex -> JSON Canvas `canvasColor`."""
    if color_hex and HEX_COLOR_RE.match(color_hex.strip()):
        return color_hex.strip()
    return PALETTE_IDX_TO_PRESET.get(int(color_idx) % len(SITH_PALETTES), "1")


# ---------------------------------------------------------------------------
# Pure-Python YAML Frontmatter Parser & Serializer (Zero Dependencies)
# ---------------------------------------------------------------------------


def _parse_yaml_scalar(raw: str) -> Any:
    val = raw.strip()
    if val == "" or val in ("null", "Null", "NULL", "~"):
        return None
    if val in ("true", "True", "TRUE"):
        return True
    if val in ("false", "False", "FALSE"):
        return False
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val[1:-1]
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(item) for item in inner.split(",")]
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    if re.fullmatch(r"-?\d+\.\d+", val):
        return float(val)
    return val


def parse_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter block at top of a Markdown file."""
    if not md_text.startswith("---"):
        return {}, md_text
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, md_text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end_idx = i
            break
    if end_idx is None:
        return {}, md_text

    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in lines[1:end_idx]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key is not None:
            meta.setdefault(current_list_key, []).append(
                _parse_yaml_scalar(stripped[2:])
            )
            continue
        current_list_key = None
        if ":" in line:
            key, _, rest = line.partition(":")
            k = key.strip()
            v = rest.strip()
            if not k:
                continue
            if v == "":
                meta[k] = []
                current_list_key = k
            else:
                meta[k] = _parse_yaml_scalar(v)

    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    return meta, body


def _format_yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if s == "" or any(ch in s for ch in (":", "#", "[", "]", "{", "}", "\n", '"')) or s.startswith(("-", " ")):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def dump_frontmatter(meta: dict[str, Any], body: str) -> str:
    """Serialize metadata dict + Markdown body back into Obsidian .md text."""
    if not meta:
        return body
    out = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            if not v:
                out.append(f"{k}: []")
            else:
                out.append(f"{k}:")
                for item in v:
                    out.append(f"  - {_format_yaml_scalar(item)}")
        else:
            out.append(f"{k}: {_format_yaml_scalar(v)}")
    out.append("---")
    out.append("")
    return "\n".join(out) + body.rstrip() + "\n"


# ---------------------------------------------------------------------------
# Markdown Analysis (Title, WikiLinks, Tags, Tasks, Subpath Extraction)
# ---------------------------------------------------------------------------


def strip_code_blocks(md_text: str) -> str:
    cleaned = FENCED_CODE_RE.sub("", md_text)
    cleaned = INLINE_CODE_RE.sub("", cleaned)
    return cleaned


def extract_markdown_features(md_text: str) -> dict[str, Any]:
    """Extract wikilinks, tags, tasks, and first heading from Markdown."""
    clean = strip_code_blocks(md_text)

    links: list[str] = []
    seen_links: set[str] = set()
    for _embed, target_raw in WIKILINK_RE.findall(clean):
        # Strip alias (|...) and heading/block (#...)
        target = target_raw.split("|", 1)[0].split("#", 1)[0].strip()
        if target and target not in seen_links:
            seen_links.add(target)
            links.append(target)

    tags: list[str] = []
    seen_tags: set[str] = set()
    for tag in TAG_RE.findall(clean):
        tl = tag.lower()
        if tl not in seen_tags:
            seen_tags.add(tl)
            tags.append(tl)

    tasks_total = 0
    tasks_done = 0
    for box, _label in TASK_RE.findall(clean):
        tasks_total += 1
        if box.lower() == "x":
            tasks_done += 1

    title = ""
    for line in md_text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            break

    return {
        "title": title,
        "links": links,
        "tags": tags,
        "tasksTotal": tasks_total,
        "tasksDone": tasks_done,
    }


def extract_subpath_section(md_text: str, subpath: str) -> str:
    """Extract a specific `#Heading` or `#^blockid` section from Markdown."""
    if not subpath or not subpath.startswith("#"):
        return md_text
    frag = subpath[1:].strip()
    if not frag:
        return md_text

    # Block reference: #^block-id
    if frag.startswith("^"):
        marker = frag
        for line in md_text.splitlines():
            if line.rstrip().endswith(marker):
                return line.rstrip()[: -len(marker)].rstrip()
        return md_text

    # Heading reference: #Heading Name
    target_norm = re.sub(r"\s+", " ", frag).strip().lower()
    lines = md_text.splitlines()
    start_idx = None
    start_level = 0
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if m:
            level = len(m.group(1))
            heading = re.sub(r"\s+", " ", m.group(2)).strip().lower()
            if start_idx is None:
                if heading == target_norm:
                    start_idx = idx
                    start_level = level
            elif level <= start_level:
                return "\n".join(lines[start_idx:idx]).strip()
    if start_idx is not None:
        return "\n".join(lines[start_idx:]).strip()
    return md_text


# ---------------------------------------------------------------------------
# JSON Canvas 1.0 Validator
# ---------------------------------------------------------------------------


def validate_canvas_data(data: Any) -> dict[str, Any]:
    """Validate an in-memory object against JSON Canvas 1.0."""
    errors: list[str] = []
    warnings: list[str] = []
    counts = {"text": 0, "file": 0, "link": 0, "group": 0, "edges": 0}

    if not isinstance(data, dict):
        return {
            "ok": False,
            "spec": SPEC_VERSION,
            "errors": ["Top-level JSON value must be an object."],
            "warnings": [],
            "stats": counts,
        }

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if not isinstance(nodes, list):
        errors.append("'nodes' must be an array.")
        nodes = []
    if not isinstance(edges, list):
        errors.append("'edges' must be an array.")
        edges = []

    seen_node_ids: set[str] = set()
    for i, node in enumerate(nodes):
        prefix = f"nodes[{i}]"
        if not isinstance(node, dict):
            errors.append(f"{prefix}: node must be an object.")
            continue

        nid = node.get("id")
        if not isinstance(nid, str) or not nid:
            errors.append(f"{prefix}: missing or non-string 'id'.")
        elif nid in seen_node_ids:
            errors.append(f"{prefix}: duplicate node id {nid!r}.")
        else:
            seen_node_ids.add(nid)

        ntype = node.get("type")
        if ntype not in VALID_NODE_TYPES:
            errors.append(f"{prefix}: invalid type {ntype!r} (expected one of {sorted(VALID_NODE_TYPES)}).")
        else:
            counts[ntype] += 1

        for coord in ("x", "y", "width", "height"):
            val = node.get(coord)
            if isinstance(val, bool) or not isinstance(val, int):
                errors.append(f"{prefix}: '{coord}' must be an integer (got {val!r}).")
            elif coord in ("width", "height") and val <= 0:
                warnings.append(f"{prefix}: '{coord}' should be positive (got {val}).")

        color = node.get("color")
        if color is not None:
            if not isinstance(color, str) or (
                color not in VALID_PRESET_COLORS and not HEX_COLOR_RE.match(color)
            ):
                errors.append(f"{prefix}: invalid canvasColor {color!r}.")

        if ntype == "text":
            if not isinstance(node.get("text"), str):
                errors.append(f"{prefix}: text node requires string 'text'.")
        elif ntype == "file":
            if not isinstance(node.get("file"), str) or not node.get("file"):
                errors.append(f"{prefix}: file node requires non-empty string 'file'.")
            subpath = node.get("subpath")
            if subpath is not None and (not isinstance(subpath, str) or not subpath.startswith("#")):
                errors.append(f"{prefix}: file node 'subpath' must start with '#'.")
        elif ntype == "link":
            if not isinstance(node.get("url"), str) or not node.get("url"):
                errors.append(f"{prefix}: link node requires non-empty string 'url'.")
        elif ntype == "group":
            bgs = node.get("backgroundStyle")
            if bgs is not None and bgs not in VALID_BG_STYLES:
                errors.append(f"{prefix}: invalid backgroundStyle {bgs!r}.")

    seen_edge_ids: set[str] = set()
    for i, edge in enumerate(edges):
        prefix = f"edges[{i}]"
        if not isinstance(edge, dict):
            errors.append(f"{prefix}: edge must be an object.")
            continue
        counts["edges"] += 1

        eid = edge.get("id")
        if not isinstance(eid, str) or not eid:
            errors.append(f"{prefix}: missing or non-string 'id'.")
        elif eid in seen_edge_ids:
            errors.append(f"{prefix}: duplicate edge id {eid!r}.")
        else:
            seen_edge_ids.add(eid)

        for ep in ("fromNode", "toNode"):
            ref = edge.get(ep)
            if not isinstance(ref, str) or not ref:
                errors.append(f"{prefix}: missing '{ep}'.")
            elif ref not in seen_node_ids:
                errors.append(f"{prefix}: '{ep}' references unknown node {ref!r}.")

        for side_key in ("fromSide", "toSide"):
            sv = edge.get(side_key)
            if sv is not None and sv not in VALID_SIDES:
                errors.append(f"{prefix}: invalid {side_key} {sv!r}.")

        for end_key in ("fromEnd", "toEnd"):
            ev = edge.get(end_key)
            if ev is not None and ev not in VALID_ENDS:
                errors.append(f"{prefix}: invalid {end_key} {ev!r}.")

        color = edge.get("color")
        if color is not None:
            if not isinstance(color, str) or (
                color not in VALID_PRESET_COLORS and not HEX_COLOR_RE.match(color)
            ):
                errors.append(f"{prefix}: invalid canvasColor {color!r}.")

    return {
        "ok": len(errors) == 0,
        "spec": SPEC_VERSION,
        "errors": errors,
        "warnings": warnings,
        "stats": counts,
    }


# ---------------------------------------------------------------------------
# Import: JSON Canvas 1.0 -> OWL 22 Deck JSON
# ---------------------------------------------------------------------------


def _infer_edge_sides(
    a: dict[str, Any], b: dict[str, Any]
) -> tuple[str, str]:
    """Compute natural anchor sides between two cards based on center delta."""
    ax = a.get("x", 0) + a.get("w", 220) / 2.0
    ay = a.get("y", 0) + a.get("h", 168) / 2.0
    bx = b.get("x", 0) + b.get("w", 220) / 2.0
    by = b.get("y", 0) + b.get("h", 168) / 2.0
    dx, dy = bx - ax, by - ay
    if abs(dx) >= abs(dy):
        return ("right", "left") if dx >= 0 else ("left", "right")
    return ("bottom", "top") if dy >= 0 else ("top", "bottom")


def import_canvas_to_deck(
    canvas_path: Path,
    vault_root: Path | None = None,
    normalize: bool = True,
    max_cards: int = 80,
) -> dict[str, Any]:
    """Convert a JSON Canvas 1.0 .canvas file into OWL holocronNotes + canvasEdges."""
    raw = json.loads(canvas_path.read_text(encoding="utf-8"))
    validation = validate_canvas_data(raw)

    nodes = raw.get("nodes", [])
    edges = raw.get("edges", [])

    # Identify group bounding boxes so enclosed cards can inherit `group` label
    groups: list[dict[str, Any]] = [
        n for n in nodes if isinstance(n, dict) and n.get("type") == "group"
    ]

    # Optional coordinate normalization (shift min x/y -> 16, 16)
    offset_x, offset_y = 0, 0
    if normalize and nodes:
        xs = [int(n.get("x", 0)) for n in nodes if isinstance(n, dict)]
        ys = [int(n.get("y", 0)) for n in nodes if isinstance(n, dict)]
        if xs and ys:
            offset_x = 16 - min(xs)
            offset_y = 16 - min(ys)

    canvas_id_to_owl_id: dict[str, int] = {}
    holocron_notes: list[dict[str, Any]] = []
    canvas_groups: list[dict[str, Any]] = []
    next_id = 1
    stamp = now_hhmm()

    for node in nodes:
        if not isinstance(node, dict):
            continue
        cid = str(node.get("id") or deterministic_canvas_id(f"node-{next_id}"))
        ntype = str(node.get("type", "text"))
        nx = int(node.get("x", 0)) + offset_x
        ny = int(node.get("y", 0)) + offset_y
        nw = max(120, int(node.get("width", 224)))
        nh = max(88, int(node.get("height", 168)))
        color_idx, color_hex = canvas_color_to_palette(node.get("color"))

        if ntype == "group":
            canvas_groups.append({
                "canvasId": cid,
                "label": str(node.get("label") or "GROUP"),
                "x": nx,
                "y": ny,
                "w": nw,
                "h": nh,
                "colorIdx": color_idx,
                "colorHex": color_hex,
                "background": str(node.get("background") or ""),
                "backgroundStyle": str(node.get("backgroundStyle") or "cover"),
            })
            continue

        if len(holocron_notes) >= max_cards:
            continue

        # Determine enclosing group label (if any)
        orig_x, orig_y = int(node.get("x", 0)), int(node.get("y", 0))
        orig_cx = orig_x + int(node.get("width", 224)) / 2.0
        orig_cy = orig_y + int(node.get("height", 168)) / 2.0
        group_label = ""
        for g in groups:
            gx, gy = int(g.get("x", 0)), int(g.get("y", 0))
            gw, gh = int(g.get("width", 0)), int(g.get("height", 0))
            if gx <= orig_cx <= gx + gw and gy <= orig_cy <= gy + gh:
                group_label = str(g.get("label") or "")
                break

        title = ""
        body_text = ""
        file_ref = ""
        subpath = ""
        url_ref = ""
        pinned = False
        extra_tags: list[str] = []

        if ntype == "text":
            body_text = str(node.get("text") or "")
        elif ntype == "file":
            file_ref = str(node.get("file") or "").replace("\\", "/")
            subpath = str(node.get("subpath") or "")
            title = Path(file_ref).stem if file_ref else "FILE"
            if vault_root and file_ref.lower().endswith(".md"):
                try:
                    fp = safe_vault_path(vault_root, file_ref)
                    if fp.is_file():
                        raw_md = fp.read_text(encoding="utf-8", errors="replace")
                        fm, md_body = parse_frontmatter(raw_md)
                        if fm.get("title"):
                            title = str(fm["title"])
                        pinned = bool(fm.get("pinned", False))
                        if isinstance(fm.get("tags"), list):
                            extra_tags = [str(t).lstrip("#").lower() for t in fm["tags"] if t]
                        body_text = extract_subpath_section(md_body, subpath) if subpath else md_body
                except (OSError, ValueError):
                    body_text = f"[[{file_ref}{subpath}]]"
            else:
                body_text = f"![[{file_ref}{subpath}]]"
        elif ntype == "link":
            url_ref = str(node.get("url") or "")
            title = url_ref.replace("https://", "").replace("http://", "").split("/", 1)[0] or "LINK"
            body_text = f"[{title}]({url_ref})"

        feats = extract_markdown_features(body_text)
        if not title:
            title = feats["title"] or f"CARD {next_id}"
        all_tags = sorted(set(extra_tags + feats["tags"]))

        owl_id = next_id
        next_id += 1
        canvas_id_to_owl_id[cid] = owl_id

        holocron_notes.append({
            "id": owl_id,
            "canvasId": cid,
            "type": ntype,
            "x": nx,
            "y": ny,
            "w": nw,
            "h": nh,
            "title": title[:64],
            "text": body_text,
            "file": file_ref,
            "subpath": subpath,
            "url": url_ref,
            "group": group_label,
            "font": "mono" if ntype == "text" else "sans",
            "fontSizeIdx": 1,
            "titleSz": 14,
            "bodySz": 11.5,
            "colorIdx": color_idx,
            "colorHex": color_hex,
            "borderIdx": 2 if pinned else 1,
            "radiusIdx": 1,
            "pinned": pinned,
            "tags": all_tags,
            "links": feats["links"],
            "tasksTotal": feats["tasksTotal"],
            "tasksDone": feats["tasksDone"],
            "time": stamp,
        })

    canvas_edges: list[dict[str, Any]] = []
    for ed in edges:
        if not isinstance(ed, dict):
            continue
        f_id = canvas_id_to_owl_id.get(str(ed.get("fromNode", "")))
        t_id = canvas_id_to_owl_id.get(str(ed.get("toNode", "")))
        if f_id is None or t_id is None:
            continue
        ec_idx, ec_hex = canvas_color_to_palette(ed.get("color"))
        canvas_edges.append({
            "id": str(ed.get("id") or deterministic_canvas_id(f"edge-{f_id}-{t_id}")),
            "from": f_id,
            "to": t_id,
            "fromSide": str(ed.get("fromSide") or ""),
            "toSide": str(ed.get("toSide") or ""),
            "fromEnd": str(ed.get("fromEnd") or "none"),
            "toEnd": str(ed.get("toEnd") or "arrow"),
            "label": str(ed.get("label") or ""),
            "colorIdx": ec_idx,
            "colorHex": ec_hex,
        })

    return {
        "ok": True,
        "action": "import-canvas",
        "source": str(canvas_path),
        "updatedAt": now_iso(),
        "validation": validation,
        "nextNoteId": next_id,
        "holocronNotes": holocron_notes,
        "canvasEdges": canvas_edges,
        "canvasGroups": canvas_groups,
        "summary": {
            "cards": len(holocron_notes),
            "edges": len(canvas_edges),
            "groups": len(canvas_groups),
        },
    }


# ---------------------------------------------------------------------------
# Export: OWL 22 Deck JSON -> JSON Canvas 1.0 (.canvas)
# ---------------------------------------------------------------------------


def export_deck_to_canvas(
    deck: dict[str, Any],
    prefer_file_nodes: bool = False,
) -> dict[str, Any]:
    """Convert OWL holocronNotes + canvasEdges into a JSON Canvas 1.0 object."""
    notes = deck.get("holocronNotes", [])
    edges = deck.get("canvasEdges", [])
    groups = deck.get("canvasGroups", [])

    canvas_nodes: list[dict[str, Any]] = []
    owl_id_to_canvas_id: dict[int, str] = {}
    owl_id_to_note: dict[int, dict[str, Any]] = {}

    # 1. Groups first (lowest z-index per JSON Canvas 1.0 ascending z-order)
    for idx, g in enumerate(groups):
        if not isinstance(g, dict):
            continue
        gid = str(g.get("canvasId") or deterministic_canvas_id(f"group-{idx}-{g.get('label', '')}"))
        gnode: dict[str, Any] = {
            "id": gid,
            "type": "group",
            "x": int(round(float(g.get("x", 0)))),
            "y": int(round(float(g.get("y", 0)))),
            "width": max(160, int(round(float(g.get("w", 320))))),
            "height": max(120, int(round(float(g.get("h", 240))))),
        }
        if g.get("label"):
            gnode["label"] = str(g["label"])
        if g.get("background"):
            gnode["background"] = str(g["background"])
            bgs = str(g.get("backgroundStyle") or "cover")
            gnode["backgroundStyle"] = bgs if bgs in VALID_BG_STYLES else "cover"
        if g.get("colorIdx") is not None or g.get("colorHex"):
            gnode["color"] = palette_to_canvas_color(
                int(g.get("colorIdx", 0)), str(g.get("colorHex", ""))
            )
        canvas_nodes.append(gnode)

    # 2. Cards (text / file / link)
    for n in notes:
        if not isinstance(n, dict):
            continue
        oid = int(n.get("id", len(owl_id_to_canvas_id) + 1))
        cid = str(n.get("canvasId") or deterministic_canvas_id(f"card-{oid}-{n.get('title', '')}"))
        owl_id_to_canvas_id[oid] = cid
        owl_id_to_note[oid] = n

        ntype = str(n.get("type") or "text")
        if prefer_file_nodes and n.get("file"):
            ntype = "file"
        elif ntype not in ("text", "file", "link"):
            ntype = "text"

        cnode: dict[str, Any] = {
            "id": cid,
            "type": ntype,
            "x": int(round(float(n.get("x", 0)))),
            "y": int(round(float(n.get("y", 0)))),
            "width": max(120, int(round(float(n.get("w", 224))))),
            "height": max(88, int(round(float(n.get("h", 168))))),
            "color": palette_to_canvas_color(
                int(n.get("colorIdx", 0)), str(n.get("colorHex", ""))
            ),
        }

        if ntype == "file" and n.get("file"):
            cnode["file"] = str(n["file"]).replace("\\", "/")
            if n.get("subpath") and str(n["subpath"]).startswith("#"):
                cnode["subpath"] = str(n["subpath"])
        elif ntype == "link" and n.get("url"):
            cnode["url"] = str(n["url"])
        else:
            cnode["type"] = "text"
            title = str(n.get("title") or "").strip()
            body = str(n.get("text") or "")
            if title and not body.lstrip().startswith("#"):
                cnode["text"] = f"# {title}\n{body}".rstrip()
            else:
                cnode["text"] = body

        canvas_nodes.append(cnode)

    # 3. Edges
    canvas_edges: list[dict[str, Any]] = []
    for idx, ed in enumerate(edges):
        if not isinstance(ed, dict):
            continue
        f_oid = ed.get("from")
        t_oid = ed.get("to")
        if f_oid not in owl_id_to_canvas_id or t_oid not in owl_id_to_canvas_id:
            continue
        from_side = str(ed.get("fromSide") or "")
        to_side = str(ed.get("toSide") or "")
        if from_side not in VALID_SIDES or to_side not in VALID_SIDES:
            from_side, to_side = _infer_edge_sides(
                owl_id_to_note[f_oid], owl_id_to_note[t_oid]
            )

        cedge: dict[str, Any] = {
            "id": str(ed.get("id") or deterministic_canvas_id(f"edge-{idx}-{f_oid}-{t_oid}")),
            "fromNode": owl_id_to_canvas_id[f_oid],
            "fromSide": from_side,
            "fromEnd": str(ed.get("fromEnd")) if ed.get("fromEnd") in VALID_ENDS else "none",
            "toNode": owl_id_to_canvas_id[t_oid],
            "toSide": to_side,
            "toEnd": str(ed.get("toEnd")) if ed.get("toEnd") in VALID_ENDS else "arrow",
        }
        if ed.get("label"):
            cedge["label"] = str(ed["label"])
        if ed.get("colorIdx") is not None or ed.get("colorHex"):
            cedge["color"] = palette_to_canvas_color(
                int(ed.get("colorIdx", 0)), str(ed.get("colorHex", ""))
            )
        canvas_edges.append(cedge)

    return {"nodes": canvas_nodes, "edges": canvas_edges}


# ---------------------------------------------------------------------------
# Vault Scanner: Local Obsidian Vault (.md) -> OWL Deck JSON + Backlinks
# ---------------------------------------------------------------------------


def scan_vault_to_deck(
    vault_root: Path,
    cols: int = 3,
    max_notes: int = 60,
) -> dict[str, Any]:
    """Scan local Obsidian .md notes, extract frontmatter/wikilinks/tags/tasks."""
    root = vault_root.resolve()
    if not root.is_dir():
        return {"ok": False, "error": f"Vault directory not found: {root}"}

    md_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_VAULT_DIRS)
        for fn in sorted(filenames):
            if fn.lower().endswith(".md"):
                md_files.append(Path(dirpath) / fn)

    md_files = md_files[:max_notes]

    holocron_notes: list[dict[str, Any]] = []
    stem_to_id: dict[str, int] = {}
    rel_to_id: dict[str, int] = {}
    alias_to_id: dict[str, int] = {}
    backlinks: dict[str, list[str]] = {}

    card_w, card_h, gap_x, gap_y = 240, 192, 32, 32
    total_tasks = 0
    done_tasks = 0

    for idx, fp in enumerate(md_files):
        owl_id = idx + 1
        rel = fp.relative_to(root).as_posix()
        stem = fp.stem
        raw = fp.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(raw)
        feats = extract_markdown_features(body)

        title = str(fm.get("title") or feats["title"] or stem)
        fm_tags = fm.get("tags", [])
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        tags = sorted(
            set([str(t).lstrip("#").lower() for t in fm_tags if t] + feats["tags"])
        )

        # Position from frontmatter if available, else tidy grid
        col = idx % max(1, cols)
        row = idx // max(1, cols)
        x = int(fm.get("canvas_x", 16 + col * (card_w + gap_x)))
        y = int(fm.get("canvas_y", 16 + row * (card_h + gap_y)))
        w = max(140, int(fm.get("canvas_w", card_w)))
        h = max(100, int(fm.get("canvas_h", card_h)))
        color_idx = int(fm.get("canvas_color", idx % len(SITH_PALETTES))) % len(SITH_PALETTES)
        pinned = bool(fm.get("pinned", False))
        cid = str(fm.get("canvas_id") or deterministic_canvas_id(rel))

        mtime_str = datetime.fromtimestamp(fp.stat().st_mtime).strftime("%H:%M")

        total_tasks += feats["tasksTotal"]
        done_tasks += feats["tasksDone"]

        holocron_notes.append({
            "id": owl_id,
            "canvasId": cid,
            "type": "file",
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "title": title[:64],
            "text": body,
            "file": rel,
            "subpath": "",
            "url": "",
            "group": str(fm.get("canvas_group") or (Path(rel).parent.as_posix() if "/" in rel else "")),
            "font": "sans",
            "fontSizeIdx": 1,
            "titleSz": 14,
            "bodySz": 11.5,
            "colorIdx": color_idx,
            "colorHex": "",
            "borderIdx": 2 if pinned else 1,
            "radiusIdx": 1,
            "pinned": pinned,
            "tags": tags,
            "links": feats["links"],
            "tasksTotal": feats["tasksTotal"],
            "tasksDone": feats["tasksDone"],
            "time": mtime_str,
        })

        stem_to_id[stem.lower()] = owl_id
        rel_to_id[rel.lower()] = owl_id
        rel_to_id[rel[:-3].lower()] = owl_id
        aliases = fm.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        for al in aliases:
            if al:
                alias_to_id[str(al).lower()] = owl_id

    # Build wikilink edges + backlink index
    canvas_edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int]] = set()
    id_to_title = {n["id"]: n["title"] for n in holocron_notes}

    for n in holocron_notes:
        src_id = n["id"]
        for link in n["links"]:
            key = link.replace("\\", "/").lower()
            dst_id = (
                rel_to_id.get(key)
                or stem_to_id.get(Path(key).stem.lower())
                or alias_to_id.get(key)
            )
            if dst_id and dst_id != src_id:
                backlinks.setdefault(id_to_title[dst_id], []).append(n["title"])
                if (src_id, dst_id) not in seen_pairs:
                    seen_pairs.add((src_id, dst_id))
                    canvas_edges.append({
                        "id": deterministic_canvas_id(f"wl-{src_id}-{dst_id}"),
                        "from": src_id,
                        "to": dst_id,
                        "fromEnd": "none",
                        "toEnd": "arrow",
                        "label": "",
                        "colorIdx": n["colorIdx"],
                    })

    return {
        "ok": True,
        "action": "scan-vault",
        "vault": str(root),
        "updatedAt": now_iso(),
        "nextNoteId": len(holocron_notes) + 1,
        "holocronNotes": holocron_notes,
        "canvasEdges": canvas_edges,
        "backlinks": backlinks,
        "vaultStats": {
            "notes": len(holocron_notes),
            "edges": len(canvas_edges),
            "tasksTotal": total_tasks,
            "tasksDone": done_tasks,
        },
    }


# ---------------------------------------------------------------------------
# Export: OWL 22 Deck JSON -> Obsidian Vault Markdown Notes (.md)
# ---------------------------------------------------------------------------


def export_deck_to_markdown(
    deck: dict[str, Any],
    vault_root: Path,
    subdir: str = "",
) -> dict[str, Any]:
    """Export OWL holocronNotes to .md files in vault_root, preserving user frontmatter."""
    root = vault_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for n in deck.get("holocronNotes", []):
        if not isinstance(n, dict):
            continue
        title = str(n.get("title") or f"Card {n.get('id', 0)}").strip()
        rel_target = str(n.get("file") or "").strip().replace("\\", "/")
        if not rel_target or not rel_target.lower().endswith(".md"):
            fname = slugify_filename(title, f"card-{n.get('id', 0)}") + ".md"
            rel_target = f"{subdir.strip('/')}/{fname}" if subdir.strip("/") else fname

        target_path = safe_vault_path(root, rel_target)
        existing_meta: dict[str, Any] = {}
        if target_path.is_file():
            existing_meta, _ = parse_frontmatter(
                target_path.read_text(encoding="utf-8", errors="replace")
            )

        body = str(n.get("text") or "")
        feats = extract_markdown_features(body)
        merged_tags = sorted(
            set(
                [str(t).lstrip("#").lower() for t in (existing_meta.get("tags") or []) if t]
                + [str(t).lstrip("#").lower() for t in (n.get("tags") or []) if t]
                + feats["tags"]
            )
        )

        updated_meta = dict(existing_meta)
        updated_meta["id"] = int(n.get("id", 0))
        updated_meta["canvas_id"] = str(
            n.get("canvasId") or deterministic_canvas_id(rel_target)
        )
        updated_meta["title"] = title
        if merged_tags:
            updated_meta["tags"] = merged_tags
        updated_meta["pinned"] = bool(n.get("pinned", False))
        updated_meta["canvas_x"] = int(round(float(n.get("x", 0))))
        updated_meta["canvas_y"] = int(round(float(n.get("y", 0))))
        updated_meta["canvas_w"] = int(round(float(n.get("w", 224))))
        updated_meta["canvas_h"] = int(round(float(n.get("h", 168))))
        updated_meta["canvas_color"] = int(n.get("colorIdx", 0))
        if n.get("group"):
            updated_meta["canvas_group"] = str(n["group"])
        updated_meta["updated"] = now_iso()

        atomic_write_text(target_path, dump_frontmatter(updated_meta, body))
        written.append(target_path.relative_to(root).as_posix())

    return {
        "ok": True,
        "action": "export-md",
        "vault": str(root),
        "writtenCount": len(written),
        "files": written,
    }


# ---------------------------------------------------------------------------
# Bidirectional Sync (Vault .canvas/.md <-> OWL deck.json)
# ---------------------------------------------------------------------------


def bidirectional_sync(
    vault_root: Path,
    canvas_path: Path,
    deck_path: Path,
    strategy: str = "newest",
) -> dict[str, Any]:
    """Sync Obsidian .canvas + .md vault with OWL deck.json using SHA-256 state."""
    root = vault_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / BRIDGE_STATE_FILE
    prev_state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            prev_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev_state = {}

    canvas_exists = canvas_path.is_file()
    deck_exists = deck_path.is_file()

    if not canvas_exists and not deck_exists:
        # Bootstrap from vault .md notes
        deck = scan_vault_to_deck(root)
        canvas_obj = export_deck_to_canvas(deck, prefer_file_nodes=True)
        atomic_write_json(canvas_path, canvas_obj)
        atomic_write_json(deck_path, deck)
        direction = "vault->both"
    elif canvas_exists and not deck_exists:
        deck = import_canvas_to_deck(canvas_path, vault_root=root, normalize=True)
        atomic_write_json(deck_path, deck)
        direction = "canvas->deck"
    elif deck_exists and not canvas_exists:
        deck = json.loads(deck_path.read_text(encoding="utf-8"))
        export_deck_to_markdown(deck, root)
        canvas_obj = export_deck_to_canvas(deck, prefer_file_nodes=False)
        atomic_write_json(canvas_path, canvas_obj)
        direction = "deck->canvas+md"
    else:
        canvas_hash = sha256_text(canvas_path.read_text(encoding="utf-8"))
        deck_hash = sha256_text(deck_path.read_text(encoding="utf-8"))
        canvas_changed = canvas_hash != prev_state.get("canvasHash")
        deck_changed = deck_hash != prev_state.get("deckHash")

        if strategy == "vault" or (
            strategy == "newest"
            and canvas_changed
            and (
                not deck_changed
                or canvas_path.stat().st_mtime >= deck_path.stat().st_mtime
            )
        ):
            deck = import_canvas_to_deck(canvas_path, vault_root=root, normalize=True)
            atomic_write_json(deck_path, deck)
            direction = "canvas->deck"
        elif strategy == "deck" or (
            strategy == "newest"
            and deck_changed
            and (
                not canvas_changed
                or deck_path.stat().st_mtime > canvas_path.stat().st_mtime
            )
        ):
            deck = json.loads(deck_path.read_text(encoding="utf-8"))
            export_deck_to_markdown(deck, root)
            canvas_obj = export_deck_to_canvas(deck, prefer_file_nodes=False)
            atomic_write_json(canvas_path, canvas_obj)
            direction = "deck->canvas+md"
        else:
            deck = json.loads(deck_path.read_text(encoding="utf-8"))
            direction = "noop"

    # Persist updated hashes
    new_state = {
        "updatedAt": now_iso(),
        "direction": direction,
        "canvasHash": sha256_text(canvas_path.read_text(encoding="utf-8")) if canvas_path.is_file() else "",
        "deckHash": sha256_text(deck_path.read_text(encoding="utf-8")) if deck_path.is_file() else "",
    }
    atomic_write_json(state_path, new_state)

    return {
        "ok": True,
        "action": "sync",
        "direction": direction,
        "strategy": strategy,
        "canvas": str(canvas_path),
        "deck": str(deck_path),
        "cards": len(deck.get("holocronNotes", [])),
        "edges": len(deck.get("canvasEdges", [])),
        "updatedAt": new_state["updatedAt"],
    }


# ---------------------------------------------------------------------------
# Demo Vault Generator
# ---------------------------------------------------------------------------


def create_demo_vault(target_dir: Path) -> dict[str, Any]:
    """Generate a ready-to-test Obsidian vault + JSON Canvas 1.0 board."""
    root = target_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".obsidian").mkdir(exist_ok=True)

    note1 = root / "Architecture.md"
    note2 = root / "Daily Ops.md"

    atomic_write_text(
        note1,
        dump_frontmatter(
            {
                "title": "Sith Deck Architecture",
                "tags": ["obsidian", "architecture"],
                "pinned": True,
                "canvas_x": 20,
                "canvas_y": 20,
                "canvas_color": 3,
            },
            "# Core Systems\n"
            "Connected to [[Daily Ops]] via JSON Canvas 1.0.\n"
            "- [x] Implement JSON Canvas 1.0 validator\n"
            "- [ ] Wire Vault Sync into OWL Forge\n"
            "#owl22\n",
        ),
    )
    atomic_write_text(
        note2,
        dump_frontmatter(
            {
                "title": "Daily Ops",
                "tags": ["daily", "tasks"],
                "canvas_x": 320,
                "canvas_y": 140,
                "canvas_color": 4,
            },
            "# Today's Checklist\n"
            "Review [[Architecture]] and verify `owl-check` frame budget.\n"
            "- [x] Zero-dependency Python bridge\n"
            "- [x] Atomic writes\n",
        ),
    )

    sample_canvas = {
        "nodes": [
            {
                "id": "g000000000000001",
                "type": "group",
                "label": "COMMAND CORE",
                "x": -40,
                "y": -40,
                "width": 720,
                "height": 420,
                "color": "6",
            },
            {
                "id": "n000000000000001",
                "type": "file",
                "file": "Architecture.md",
                "subpath": "#Core Systems",
                "x": 0,
                "y": 0,
                "width": 280,
                "height": 200,
                "color": "6",
            },
            {
                "id": "n000000000000002",
                "type": "file",
                "file": "Daily Ops.md",
                "x": 340,
                "y": 120,
                "width": 280,
                "height": 190,
                "color": "4",
            },
            {
                "id": "n000000000000003",
                "type": "text",
                "text": "# Quick Note\n==Obsidian Canvas== synced with **OWL 22**.\n#sith",
                "x": 0,
                "y": 240,
                "width": 260,
                "height": 140,
                "color": "#ff1738",
            },
            {
                "id": "n000000000000004",
                "type": "link",
                "url": "https://jsoncanvas.org/spec/1.0/",
                "x": 340,
                "y": -20,
                "width": 260,
                "height": 100,
                "color": "5",
            },
        ],
        "edges": [
            {
                "id": "e000000000000001",
                "fromNode": "n000000000000001",
                "fromSide": "right",
                "fromEnd": "none",
                "toNode": "n000000000000002",
                "toSide": "left",
                "toEnd": "arrow",
                "label": "feeds",
                "color": "6",
            },
            {
                "id": "e000000000000002",
                "fromNode": "n000000000000001",
                "fromSide": "bottom",
                "toNode": "n000000000000003",
                "toSide": "top",
                "toEnd": "arrow",
                "color": "1",
            },
        ],
    }

    canvas_path = root / "CommandDeck.canvas"
    atomic_write_json(canvas_path, sample_canvas)
    return {
        "ok": True,
        "action": "demo",
        "vault": str(root),
        "canvas": str(canvas_path),
        "notes": [str(note1), str(note2)],
    }


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="06-vault-bridge.py",
        description="Obsidian JSON Canvas 1.0 & Markdown Vault Bridge for OWL 22.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import-canvas", help="Import .canvas -> OWL deck JSON")
    p_imp.add_argument("--canvas", required=True, help="Path to .canvas file")
    p_imp.add_argument("--vault", default="", help="Optional Obsidian vault root for file nodes")
    p_imp.add_argument("--normalize", action="store_true", help="Shift min x/y to (16, 16)")
    p_imp.add_argument("--out", default="", help="Optional output deck JSON path")

    p_exp = sub.add_parser("export-canvas", help="Export OWL deck JSON -> .canvas")
    p_exp.add_argument("--in", dest="in_path", required=True, help="Input OWL deck JSON (or '-' for stdin)")
    p_exp.add_argument("--out", required=True, help="Output .canvas file path")
    p_exp.add_argument("--prefer-files", action="store_true", help="Emit file nodes when card.file is set")

    p_scan = sub.add_parser("scan-vault", help="Scan Obsidian vault .md -> OWL deck JSON")
    p_scan.add_argument("--vault", required=True, help="Obsidian vault root directory")
    p_scan.add_argument("--cols", type=int, default=3, help="Grid columns for unpositioned notes")
    p_scan.add_argument("--max-notes", type=int, default=60, help="Max notes to import")
    p_scan.add_argument("--out", default="", help="Optional output deck JSON path")

    p_md = sub.add_parser("export-md", help="Export OWL deck cards -> Obsidian .md notes")
    p_md.add_argument("--in", dest="in_path", required=True, help="Input OWL deck JSON")
    p_md.add_argument("--vault", required=True, help="Target Obsidian vault root")
    p_md.add_argument("--subdir", default="", help="Optional subfolder inside vault")

    p_sync = sub.add_parser("sync", help="Bidirectional sync between Vault and OWL deck JSON")
    p_sync.add_argument("--vault", required=True, help="Obsidian vault root directory")
    p_sync.add_argument("--canvas", required=True, help="Path to .canvas file")
    p_sync.add_argument("--deck", required=True, help="Path to OWL deck JSON file")
    p_sync.add_argument("--strategy", choices=["newest", "vault", "deck"], default="newest")

    p_val = sub.add_parser("validate", help="Validate a JSON Canvas 1.0 .canvas file")
    p_val.add_argument("--canvas", required=True, help="Path to .canvas file")

    p_demo = sub.add_parser("demo", help="Create a sample Obsidian vault + .canvas")
    p_demo.add_argument("--dir", required=True, help="Target directory for demo vault")

    return p


def _load_json_input(path_str: str) -> Any:
    if path_str == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path_str).expanduser().read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd == "import-canvas":
            canvas_p = Path(args.canvas).expanduser()
            vault_p = Path(args.vault).expanduser() if args.vault else None
            res = import_canvas_to_deck(canvas_p, vault_root=vault_p, normalize=args.normalize)
            if args.out:
                atomic_write_json(Path(args.out).expanduser(), res)
            print(json.dumps(res, ensure_ascii=False))
            return 0

        if args.cmd == "export-canvas":
            deck = _load_json_input(args.in_path)
            canvas_obj = export_deck_to_canvas(deck, prefer_file_nodes=args.prefer_files)
            val = validate_canvas_data(canvas_obj)
            out_p = Path(args.out).expanduser()
            atomic_write_json(out_p, canvas_obj)
            print(json.dumps({
                "ok": val["ok"],
                "action": "export-canvas",
                "out": str(out_p),
                "validation": val,
            }, ensure_ascii=False))
            return 0 if val["ok"] else 1

        if args.cmd == "scan-vault":
            res = scan_vault_to_deck(
                Path(args.vault).expanduser(),
                cols=args.cols,
                max_notes=args.max_notes,
            )
            if args.out and res.get("ok"):
                atomic_write_json(Path(args.out).expanduser(), res)
            print(json.dumps(res, ensure_ascii=False))
            return 0 if res.get("ok") else 1

        if args.cmd == "export-md":
            deck = _load_json_input(args.in_path)
            res = export_deck_to_markdown(
                deck, Path(args.vault).expanduser(), subdir=args.subdir
            )
            print(json.dumps(res, ensure_ascii=False))
            return 0

        if args.cmd == "sync":
            res = bidirectional_sync(
                Path(args.vault).expanduser(),
                Path(args.canvas).expanduser(),
                Path(args.deck).expanduser(),
                strategy=args.strategy,
            )
            print(json.dumps(res, ensure_ascii=False))
            return 0

        if args.cmd == "validate":
            raw = json.loads(Path(args.canvas).expanduser().read_text(encoding="utf-8"))
            res = validate_canvas_data(raw)
            print(json.dumps(res, ensure_ascii=False))
            return 0 if res["ok"] else 1

        if args.cmd == "demo":
            res = create_demo_vault(Path(args.dir).expanduser())
            print(json.dumps(res, ensure_ascii=False))
            return 0

    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
