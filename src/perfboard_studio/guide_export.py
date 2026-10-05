"""Rendering the soldering guide to the formats PLAN.md Sec 7.6 asks for.

Three writers, one model. ``guide.py`` decides WHAT the guide says; this file decides
what it looks like, and knows nothing about perfboard.

  HTML   one self-contained file. No CDN, no fonts, no network: it is meant to be opened
         on a phone propped against the monitor in a room that may not have wifi, and to
         still work in five years. Steps tick off and the progress is kept in
         localStorage, keyed by the document name, so closing the tab mid-build does not
         lose the place.
  CSV    the cut list and the BOM -- the two things people paste into a spreadsheet or
         hand to a supplier.
  JSON   the whole guide, for agents and integrations. Stable key order.

Pure and deterministic, stdlib only: no Qt, no I/O of its own beyond returning strings.
That is what lets the MCP server and a headless CI run produce the same guide the
desktop app does.

Every word this file adds -- a heading, a column, the tag beside a step -- is said in the
guide's own language (``guide.language``, through ``phrasebook``), so a Turkish guide is
Turkish all the way through without anybody passing the language twice. The ids the
guide carries as DATA (an archetype, a checkpoint kind, a wire colour) stay ids in the
JSON and are said in words where a person reads them.

The 1:1 printable sheets are a different thing and live in ui/export_pdf.py, because
they need a real renderer. This file references them rather than reproducing them.
"""

from __future__ import annotations

import base64
import csv
import io
import itertools
import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from html import escape
from typing import Any

from .drc import MATERIAL_LABELS
from .geometry import board_size_mm, format_hole
from .guide import (
    WIRE_TEMPLATE_MARGIN_MM,
    Checkpoint,
    ConductorStep,
    Guide,
    GuideStep,
    PartStep,
    WireTemplate,
    all_steps,
    conductor_word,
    step_focus,
    wire_template,
)
from .model import HoleCoord
from .phrasebook import Phrasebook, phrasebook
from .version import __version__

# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def guide_to_json(guide: Guide, indent: int = 2) -> str:
    """The whole guide as JSON, for agents and integrations.

    Hole coordinates are emitted BOTH ways -- ``{"col": 2, "row": 6, "ref": "C7"}`` --
    because a consumer needs the numbers and a human reading the file needs the address,
    and making either one derive the other means two implementations of the hole
    encoding in the wild.
    """
    head: dict[str, Any] = {
        "generator": f"Perfboard Studio {__version__}",
        "document": guide.document_name,
    }
    # Only when it is not English -- the stripAxis rule, applied to an export: every
    # English guide ever written is still byte for byte what it was, and a reader that
    # finds no language knows it is reading English.
    if guide.language != "en":
        head["language"] = guide.language
    return json.dumps(
        {
            **head,
            "board": {
                "cols": guide.board.cols,
                "rows": guide.board.rows,
                "pitch_mm": guide.board.pitch,
                "material": guide.board.material,
                "thickness_mm": guide.board.thickness,
            },
            "iron": _plain(guide.iron),
            "tools": list(guide.tools),
            "warnings": [_plain(w) for w in guide.warnings],
            "bom": [_plain(line) for line in guide.bom],
            "cut_list": [_plain(cut) for cut in guide.cut_list],
            "spine_list": [_plain(spine) for spine in guide.spine_list],
            "track_cuts": [_plain(cut) for cut in guide.track_cuts],
            "phases": [
                {
                    "number": phase.number,
                    "title": phase.title,
                    "summary": phase.summary,
                    "steps": [_plain(step) for step in phase.steps],
                    "checkpoints": [_plain(check) for check in phase.checkpoints],
                }
                for phase in guide.phases
            ],
            "totals": {
                "part_steps": guide.part_steps,
                "conductor_steps": guide.conductor_steps,
                "checkpoints": guide.checkpoint_count,
            },
        },
        indent=indent,
        ensure_ascii=False,
    )


def _plain(value: Any) -> Any:
    """Dataclasses to dicts, with every HoleCoord carrying its own address.

    Walks the fields itself rather than calling ``dataclasses.asdict``, which recurses
    first and would hand back holes already flattened to ``{"col", "row"}`` -- the
    address would be silently missing from every nested step.
    """
    if isinstance(value, HoleCoord):
        return {"col": value.col, "row": value.row, "ref": format_hole(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

#: Column headings, said in the guide's language like every other word here. Named here,
#: rather than written where they are used, because they reach ``say`` one at a time
#: through a loop -- and ``tests/test_phrasebook.py`` reads these to know they are said.
CUT_LIST_COLUMNS: tuple[str, ...] = (
    "type", "net", "from", "to", "path_mm", "cut_mm", "strip_mm", "awg", "colour", "note",
)
BOM_COLUMNS: tuple[str, ...] = ("quantity", "value", "footprint", "references")


def cut_list_to_csv(guide: Guide) -> str:
    """The wire cut list (PLAN.md Sec 7.3), plus the spine wires as their own rows.

    One table rather than two files: at the bench they are the same job -- cut these
    lengths of this wire -- and the ``type`` column is enough to tell them apart.
    """
    say = phrasebook(guide.language)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([say(column) for column in CUT_LIST_COLUMNS])
    for cut in guide.cut_list:
        writer.writerow(
            [
                _wire_type(cut.insulated, say),
                cut.net_name,
                format_hole(cut.from_hole),
                format_hole(cut.to_hole),
                f"{cut.path_mm:.1f}",
                f"{cut.cut_mm:.1f}",
                f"{cut.strip_mm:.1f}",
                cut.awg,
                say(cut.colour),
                "",
            ]
        )
    for spine in guide.spine_list:
        writer.writerow(
            [
                say("trace spine"),
                spine.net_name,
                "",
                "",
                f"{spine.length_mm:.1f}",
                f"{spine.length_mm:.1f}",
                "0.0",
                "",
                say(spine.material),
                say("{gauge:g} mm, laid along {pads} pads", gauge=spine.gauge_mm, pads=spine.pads),
            ]
        )
    return out.getvalue()


def _wire_type(insulated: bool, say: Phrasebook) -> str:
    """One spelling for the cut list's two kinds of wire, in the CSV and the HTML table."""
    return say("insulated wire") if insulated else say("bare wire")


def bom_to_csv(guide: Guide) -> str:
    say = phrasebook(guide.language)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([say(column) for column in BOM_COLUMNS])
    for line in guide.bom:
        writer.writerow([line.quantity, line.value, line.footprint_name, line.refs])
    return out.getvalue()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

#: The wire table's headings -- the same list as the cut-list CSV, for a person.
WIRE_TABLE_COLUMNS: tuple[str, ...] = ("Type", "Net", "From", "To", "Cut", "AWG", "Colour")

_STYLE = """
:root {
  --bg: #14161b; --panel: #1b1e25; --panel-2: #22262f; --line: #2e333d;
  --text: #e7e9ee; --dim: #9aa3b2; --accent: #6fb3ff; --ok: #57c785;
  --warn: #f0b34a; --error: #ef6b6b; --trace: #d8a44a;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --panel-2: #f0f2f5; --line: #d9dee6;
    --text: #1a1d23; --dim: #5d6673; --accent: #1667c8; --ok: #1d7a49;
    --warn: #8a5a00; --error: #b32020; --trace: #8a6100;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 54rem; margin: 0 auto; padding: 1.5rem 1rem 6rem; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.2rem; margin: 2.5rem 0 .25rem; }
h3 { font-size: 1rem; margin: 1.5rem 0 .5rem; color: var(--dim);
     text-transform: uppercase; letter-spacing: .06em; }
p  { margin: .4rem 0; }
a  { color: var(--accent); }
.sub { color: var(--dim); }
.phase { border-top: 2px solid var(--line); padding-top: .5rem; margin-top: 2rem; }
.phase-num { color: var(--accent); font-variant-numeric: tabular-nums; }
.step, .check {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: .7rem .9rem; margin: .5rem 0; display: flex; gap: .75rem; align-items: start;
}
.step.done, .check.done { opacity: .45; }
.step.done .title { text-decoration: line-through; }
input[type=checkbox] { width: 1.25rem; height: 1.25rem; margin-top: .2rem; flex: none; }
.body { flex: 1; min-width: 0; }
.title { font-weight: 600; }
.meta { color: var(--dim); font-size: .875rem; }
.note { font-size: .9rem; margin-top: .35rem; }
.shot { display: block; width: 100%; max-width: 30rem; margin: .6rem 0 .1rem;
        border: 1px solid var(--line); border-radius: 8px; background: #14161b; }
.hole { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        background: var(--panel-2); border-radius: 4px; padding: 0 .3rem; }
.tag { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
       border: 1px solid var(--line); border-radius: 999px; padding: .1rem .5rem;
       color: var(--dim); white-space: nowrap; }
.polarity { color: var(--warn); font-weight: 600; }
.risk { color: var(--error); }
.check { border-left: 3px solid var(--ok); }
.check.isolation { border-left-color: var(--warn); }
.check.blocking { border-left-color: var(--error); }
.warnbox { background: var(--panel-2); border-left: 3px solid var(--warn);
           border-radius: 6px; padding: .6rem .9rem; margin: .5rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; margin: .5rem 0; }
th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--line); }
th { color: var(--dim); font-weight: 600; }
.wrap { overflow-x: auto; }
.progress { position: fixed; left: 0; right: 0; bottom: 0; background: var(--panel);
            border-top: 1px solid var(--line); padding: .6rem 1rem;
            display: flex; gap: 1rem; align-items: center; justify-content: center; }
.bar { flex: 1; max-width: 30rem; height: 8px; background: var(--panel-2);
       border-radius: 999px; overflow: hidden; }
.bar > i { display: block; height: 100%; width: 0; background: var(--ok); }
button { font: inherit; color: var(--text); background: var(--panel-2);
         border: 1px solid var(--line); border-radius: 6px; padding: .3rem .7rem;
         cursor: pointer; }
@media print {
  /* The whole palette, not just body: printed from a browser whose OS is in dark mode
     the tokens above are still the dark ones, and browsers drop background colours when
     they print -- so --dim's pale grey lands on white paper and the meta line under
     every step is the part that fades out. This guide gets taped next to the board. */
  :root {
    --bg: #fff; --panel: #fff; --panel-2: #f0f0f0; --line: #bbb;
    --text: #000; --dim: #444; --accent: #10305c; --ok: #14562f;
    --warn: #6b4300; --error: #8c1616; --trace: #6b4b00;
  }
  .progress, input[type=checkbox] { display: none; }
  body { background: #fff; color: #000; }
  .step, .check { break-inside: avoid; }
  .shot { break-inside: avoid; max-width: 20rem; }
  /* The preview in a step is for the screen; paper gets the 1:1 sheets at the end. */
  .tpl-preview { display: none; }
  .templates { break-before: page; }
}
.tpl-preview svg { display: block; max-width: 100%; height: auto; margin: .5rem 0 .1rem;
                   background: #fff; border-radius: 6px; }
/* The 1:1 sheet packs its templates in rows: each is as wide as its own drawings
   (min-content, so the caption wraps under them instead of widening the cell), and a
   row takes as many as fit across the page. One per line, atmega328-relay's 53 wires
   printed on ten pages; in rows, five. */
.tpl-grid { display: flex; flex-wrap: wrap; gap: 4mm 5mm; align-items: flex-start;
            margin-top: 4mm; }
.tpl { break-inside: avoid; width: min-content; }
/* The title is what sets a small wire's cell width, so it is the size that decides how
   many fit across a page. */
.tpl .title { white-space: nowrap; font-size: .8rem; }
.tpl .title .hole { padding: 0 .15rem; }
.tpl .meta { font-size: .7rem; line-height: 1.3; }
.tpl-pair { display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; align-items: flex-start; }
.tpl svg, .ruler { display: block; background: #fff; }
@media screen { .tpl svg { max-width: 100%; height: auto; } }
"""

_SCRIPT = """
(function () {
  var key = 'perfboard-studio-guide:' + document.body.dataset.doc;
  var boxes = Array.prototype.slice.call(document.querySelectorAll('input[type=checkbox]'));
  var fill = document.querySelector('.bar > i');
  var count = document.querySelector('.count');
  var done = {};
  try { done = JSON.parse(localStorage.getItem(key) || '{}'); } catch (e) { done = {}; }

  function paint() {
    var n = 0;
    boxes.forEach(function (box) {
      box.closest('.step, .check').classList.toggle('done', box.checked);
      if (box.checked) n++;
    });
    if (fill) fill.style.width = (boxes.length ? (n / boxes.length) * 100 : 0) + '%';
    if (count) count.textContent = n + '__OF__' + boxes.length + '__DONE__';
  }

  boxes.forEach(function (box) {
    box.checked = !!done[box.id];
    box.addEventListener('change', function () {
      done[box.id] = box.checked;
      try { localStorage.setItem(key, JSON.stringify(done)); } catch (e) {}
      paint();
    });
  });

  var reset = document.querySelector('.reset');
  if (reset) reset.addEventListener('click', function () {
    done = {};
    try { localStorage.removeItem(key); } catch (e) {}
    boxes.forEach(function (box) { box.checked = false; });
    paint();
  });

  paint();
})();
"""


def guide_to_html(guide: Guide, step_images: Mapping[str, bytes] | None = None) -> str:
    """One self-contained HTML file: no network, no assets, no build step.

    Deliberately not a framework. This file has to open from a USB stick on a phone in
    five years' time, which rules out every dependency that could stop existing.

    ``step_images`` are the illustrations PLAN.md §7.2 asks for, keyed by
    ``guide.step_focus(step)``. **Raw image bytes, not URLs or paths** — this function
    base64s them into the document itself. That is the whole point: a caller cannot hand
    it a link, so the finished file cannot acquire a dependency on a server, a folder
    beside it, or a phone's network. Rendering them needs VTK and a real board, which is
    the host's job (``ui/view3d.render_offscreen``); this module has never known what a
    board looks like and still does not — including what format its pictures are in,
    which ``_image_media_type`` reads off the bytes rather than agreeing in advance.
    """
    say = phrasebook(guide.language)
    parts: list[str] = [
        "<!doctype html>",
        f'<html lang="{guide.language}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>"
        + say("{name} — build guide", name=escape(guide.document_name))
        + "</title>",
        f"<style>{_STYLE}</style></head>",
        f'<body data-doc="{escape(guide.document_name)}"><main>',
        f"<h1>{escape(guide.document_name)}</h1>",
        # Spelled the way the sheet below it spells them: "32 × 22" with spaces, and the
        # material as the guide's own prose writes it (FR-4, not the enum's FR4). The
        # header and the "Cut it to..." line twelve lines down described one board in two
        # notations.
        '<p class="sub">'
        + say(
            "Soldering guide · {cols} × {rows} {material} perfboard at {pitch:g} mm pitch · "
            "{steps} steps, {checks} checks · Perfboard Studio {version}",
            cols=guide.board.cols,
            rows=guide.board.rows,
            material=escape(MATERIAL_LABELS[guide.board.material]),
            pitch=guide.board.pitch,
            steps=guide.total_steps,
            checks=guide.checkpoint_count,
            version=escape(__version__),
        )
        + "</p>",
    ]

    for warning in guide.warnings:
        parts.append(
            f'<div class="warnbox"><b>{escape(say(warning.code))}</b> — '
            f"{escape(warning.message)}</div>"
        )

    parts.append(_html_preparation(guide, say))

    images = step_images or {}
    step_id = 0
    for phase in guide.phases:
        if phase.is_empty or phase.number == 0:
            continue
        parts.append(
            '<section class="phase"><h2><span class="phase-num">'
            + say("Phase {number}", number=phase.number)
            + f"</span> — {escape(phase.title)}</h2>"
            f'<p class="sub">{escape(phase.summary)}</p>'
        )
        for step in phase.steps:
            step_id += 1
            parts.append(_html_step(step, f"s{step_id}", images, say, guide))
        if phase.checkpoints:
            parts.append("<h3>" + say("Check before moving on") + "</h3>")
            for check in phase.checkpoints:
                step_id += 1
                parts.append(_html_check(check, f"s{step_id}", say))
        parts.append("</section>")

    parts.append(_html_tables(guide, say))
    parts.append(_html_templates(guide, say))
    # The counter's words go into the script as JavaScript string literals, which is why
    # a translation of them may hold no quote or backslash (tests/test_phrasebook.py).
    script = _SCRIPT.replace("__OF__", say(" of ")).replace("__DONE__", say(" done"))
    parts.append(
        '</main><div class="progress"><span class="count"></span>'
        '<span class="bar"><i></i></span>'
        '<button class="reset" type="button">'
        + say("Reset progress")
        + "</button></div>"
        f"<script>{script}</script></body></html>"
    )
    return "\n".join(parts)


def _hole(at: HoleCoord) -> str:
    return f'<span class="hole">{escape(format_hole(at))}</span>'


def _html_cuts(guide: Guide, say: Phrasebook) -> str:
    """The tracks to break, before anything is soldered.

    In phase 0 and nowhere else, because that is when they are physically possible: the
    cut is drilled through the hole from the copper side, and a part sitting over it puts
    the hole out of a drill bit's reach for good.
    """
    if not guide.track_cuts:
        return ""
    rows = "".join(
        f"<tr><td>{_hole(cut.at)}</td><td>{escape(cut.strip)}</td>"
        f"<td>{_hole(cut.separates[0])} ↔ {_hole(cut.separates[1])}</td></tr>"
        if cut.separates is not None
        else f"<tr><td>{_hole(cut.at)}</td><td>{escape(cut.strip)}</td><td>—</td></tr>"
        for cut in guide.track_cuts
    )
    return (
        "<h3>" + say("Cut these tracks first") + "</h3><p>"
        + say(
            "{count} cut(s), made from the copper side with a spot-face cutter or a 3 mm "
            "drill turned by hand. Each one takes the pad with it, so the hole it is made in "
            "has nothing to solder to afterwards. Do them all before the first part goes in "
            "— once a part is over a hole there is no way back to it.",
            count=len(guide.track_cuts),
        )
        + '</p><div class="wrap"><table>'
        f"<tr><th>{say('Hole')}</th><th>{say('Strip')}</th><th>{say('Separates')}</th></tr>"
        f"{rows}</table></div>"
    )


def _board_mm(guide: Guide) -> str:
    """The substrate's real size, as ``W × H mm``."""
    width, height = board_size_mm(guide.board)
    return f"{width:.1f} × {height:.1f} mm"


def _html_preparation(guide: Guide, say: Phrasebook) -> str:
    phase = guide.phases[0]
    rows = "".join(f"<li>{escape(tool)}</li>" for tool in guide.tools)
    checks = "".join(
        _html_check(check, f"p0-{n}", say) for n, check in enumerate(phase.checkpoints)
    )
    return (
        '<section class="phase"><h2><span class="phase-num">'
        + say("Phase {number}", number=0)
        + f"</span> — {escape(phase.title)}</h2>"
        f'<p class="sub">{escape(phase.summary)}</p>'
        f"<h3>{say('On the bench')}</h3><ul>{rows}</ul>"
        f"<h3>{say('Iron')}</h3><p>"
        + say(
            "{temperature} °C, no more than {dwell:g} s on any one pad. {note}",
            temperature=guide.iron.temperature_c,
            dwell=guide.iron.max_dwell_s,
            note=escape(guide.iron.note),
        )
        + f"</p><h3>{say('The board')}</h3><p>"
        + say(
            "Cut it to {cols} × {rows} holes ({size}) and mark hole {a1} in the top-left "
            "corner on the COMPONENT side. Every address below is counted from it, so if it "
            "is marked wrong, everything else is.",
            cols=guide.board.cols,
            rows=guide.board.rows,
            # geometry.board_size_mm, never cols*pitch: the substrate runs half a pitch
            # past the outermost hole centres PLUS the printed border, and its docstring
            # says nothing else may recompute it. Cutting to the recomputed number cuts a
            # bordered board short by the border on both sides.
            size=_board_mm(guide),
            a1='<span class="hole">A1</span>',
        )
        + f"</p>{_html_cuts(guide, say)}{checks}</section>"
    )


def _html_step(
    step: GuideStep,
    dom_id: str,
    images: Mapping[str, bytes],
    say: Phrasebook,
    guide: Guide | None = None,
) -> str:
    picture = _html_step_image(step, images)
    if isinstance(step, PartStep):
        return _html_part_step(step, dom_id, picture, say)
    template = wire_template(step, guide.board) if guide is not None else None
    return _html_conductor_step(step, dom_id, picture, say, template)


#: Magic bytes to media type, longest signature first. A data URI carries its own type,
#: so the one thing this must never do is guess: a JPEG announced as ``image/png`` is a
#: broken picture in the one place there is no network to re-fetch it from.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # bytes 8..12 say WEBP; _image_media_type checks them
    (b"\x89PNG", "image/png"),  # the truncated stub the tests hand over
)


def _image_media_type(data: bytes) -> str:
    """What the bytes actually are, read off the front of them.

    The renderer's format is its own decision (``ui/view3d`` writes JPEG, and wrote PNG
    before that), and this module has no way to ask. Sniffing keeps the two from having
    to agree in advance -- and keeps a stale caller from mislabelling its own picture.
    """
    for magic, media in _IMAGE_MAGIC:
        if not data.startswith(magic):
            continue
        if magic == b"RIFF" and data[8:12] != b"WEBP":
            continue
        return media
    return "application/octet-stream"


def _html_step_image(step: GuideStep, images: Mapping[str, bytes]) -> str:
    """The board as it stands at this step, with this step's own part picked out.

    Inlined as a data URI. The alt text is the step's title rather than "step image":
    printed, or read aloud, or opened where the picture will not load, the sentence that
    survives has to be the one that says what to do.
    """
    picture = images.get(step_focus(step))
    if not picture:
        return ""
    encoded = base64.b64encode(picture).decode("ascii")
    return (
        f'<img class="shot" alt="{escape(step.title)}" '
        f'src="data:{_image_media_type(picture)};base64,{encoded}">'
    )


def _html_part_step(
    step: PartStep, dom_id: str, picture: str = "", say: Phrasebook | None = None
) -> str:
    say = say or phrasebook("en")
    holes = " ".join(f"{escape(number)}:{_hole(at)}" for number, at in step.pin_holes)
    bits = [
        f'<div class="title">{escape(step.title)}</div>',
        '<div class="meta">'
        + say("{footprint} · pins {holes}", footprint=escape(step.footprint_name), holes=holes)
        + (f" · {step.rotation}°" if step.rotation else "")
        + "</div>",
    ]
    if step.bend_template_mm:
        bits.append(
            '<div class="note">'
            + say(
                "Bend the leads to {pitch:.2f} mm ({holes:.0f} holes).",
                pitch=step.bend_template_mm,
                holes=step.bend_template_mm / 2.54,
            )
            + "</div>"
        )
    if step.polarity:
        bits.append(f'<div class="note polarity">{escape(step.polarity)}</div>')
    for note in step.notes:
        bits.append(f'<div class="note sub">{escape(note)}</div>')
    return (
        f'<label class="step" for="{dom_id}">'
        f'<input type="checkbox" id="{dom_id}">'
        f'<span class="body">{"".join(bits)}{picture}</span>'
        f'<span class="tag">{escape(say(step.archetype))}</span></label>'
    )


def _conductor_word(step: ConductorStep, say: Phrasebook) -> str:
    """What the copper is made of, as the tag beside the step and its meta line say it."""
    return conductor_word(step.conductor_kind, say.language)


def _html_conductor_step(
    step: ConductorStep,
    dom_id: str,
    picture: str = "",
    say: Phrasebook | None = None,
    template: WireTemplate | None = None,
) -> str:
    say = say or phrasebook("en")
    bits = [
        f'<div class="title">{escape(step.net_name)}: '
        f"{_hole(step.path[0])} → {_hole(step.path[-1])}</div>"
        if step.path
        else f'<div class="title">{escape(step.title)}</div>',
        f'<div class="meta">{escape(_conductor_word(step, say))} · '
        f"{step.length_mm:.1f} mm"
        + (" · " + say("{pads} pads", pads=step.pads) if step.pads > 2 else "")
        + (
            " · " + say("about {milliohm:.1f} mΩ", milliohm=step.resistance_ohm * 1000)
            if step.resistance_ohm is not None
            else ""
        )
        + (
            " · " + say("{drop:.0f} mV drop", drop=step.drop_mv)
            if step.drop_mv is not None
            else ""
        )
        + "</div>",
    ]
    # The path's own length, not its pad count: a wire's corners are where to bend it, and
    # they are exactly what a wire laid along the grid most needs to say.
    if len(step.path) > 2:
        bits.append(
            '<div class="meta">'
            + say("Path: {holes}", holes=" → ".join(_hole(at) for at in step.path))
            + "</div>"
        )
    if step.cut is not None:
        # Bare wire has nothing to strip, and "strip 0 mm at each end" is an instruction
        # to do nothing -- so the clause is left out rather than printed as a zero.
        strip = (
            say(", strip {strip:.0f} mm at each end", strip=step.cut.strip_mm)
            if step.cut.strip_mm > 0
            else ""
        )
        bits.append(
            '<div class="note">'
            + say(
                "Cut {length:.0f} mm of {colour} AWG {awg}{strip}.",
                length=step.cut.cut_mm,
                colour=escape(say(step.cut.colour)),
                awg=step.cut.awg,
                strip=strip,
            )
            + "</div>"
        )
    if step.spine is not None:
        bits.append(
            '<div class="note">'
            + say(
                "Spine: {length:.0f} mm of {gauge:g} mm {material}.",
                length=step.spine.length_mm,
                gauge=step.spine.gauge_mm,
                material=escape(say(step.spine.material)),
            )
            + "</div>"
        )
    for note in step.notes:
        bits.append(f'<div class="note sub">{escape(note)}</div>')
    if template is not None:
        bits.append(f'<div class="tpl-preview">{_template_pair(template, step, PREVIEW_SCALE)}</div>')
    for risk in step.risks:
        bits.append(f'<div class="note risk">⚠ {escape(risk.message)}</div>')
    return (
        f'<label class="step" for="{dom_id}">'
        f'<input type="checkbox" id="{dom_id}">'
        f'<span class="body">{"".join(bits)}{picture}</span>'
        f'<span class="tag" style="color:var(--trace)">'
        f"{escape(_conductor_word(step, say))}</span></label>"
    )


def _html_check(check: Checkpoint, dom_id: str, say: Phrasebook | None = None) -> str:
    say = say or phrasebook("en")
    classes = f"check {check.kind}" + (" blocking" if check.blocking else "")
    holes = (
        '<div class="meta">'
        + say("Probe {holes}", holes=say.joined([_hole(at) for at in check.holes]))
        + "</div>"
        if check.holes
        else ""
    )
    gate = (
        '<div class="note risk">' + say("Do not apply power until this passes.") + "</div>"
        if check.blocking
        else ""
    )
    return (
        f'<label class="{classes}" for="{dom_id}">'
        f'<input type="checkbox" id="{dom_id}">'
        f'<span class="body"><div class="title">{escape(check.title)}</div>'
        f'<div class="note">{escape(check.instruction)}</div>{holes}'
        '<div class="note sub">'
        + say("Expect: {expected}", expected=escape(check.expected))
        + f"</div>{gate}</span>"
        f'<span class="tag">{escape(say(check.kind))}</span></label>'
    )


#: Insulation colours as ink. Keyed on the guide's colour ids (``guide.SIGNAL_COLORS`` and
#: ``COLOR_BY_NET_CLASS``); a colour this does not know is drawn grey rather than refused.
_WIRE_INK: dict[str, str] = {
    "red": "#d62828", "black": "#222222", "yellow": "#e9b91c", "green": "#2a9d4f",
    "blue": "#1d6fd8", "white": "#f4f4f4", "orange": "#f07c1c", "violet": "#8a4fd0",
    "grey": "#8c8c8c", "brown": "#7a4a22",
}
_BARE_INK = "#9a9a9a"
_COPPER_INK = "#c87533"
#: Drawn widths, in mm. Roughly the wire's own -- what matters is that the bends and the
#: strip marks read at arm's length, not the gauge.
_INSULATED_MM = 1.2
_BARE_MM = 0.6
#: A generous width per character of a 2.6 mm label, for deciding which side it fits on.
_LABEL_EM_MM = 1.8


#: How much bigger than life the preview inside a step is drawn. A screen has no real
#: millimetres anyway, and at 1:1 a 20 mm wire is a smudge beside its instructions.
PREVIEW_SCALE = 3


def _template_svg(template: WireTemplate, step: ConductorStep, scale: float = 1) -> str:
    """One wire's cut-and-bend pattern as SVG, sized in real millimetres.

    The width and height carry ``mm`` and the viewBox is in mm, so printed at 100 % the
    drawing IS the wire: lay it on the paper, bend where the rings are, cut at the tips.
    The grid under it is the board's own holes, so a corner can be checked against them.
    ``scale`` enlarges the drawing without changing the viewBox -- for the preview only.
    """
    cut = step.cut
    assert cut is not None
    ink = _WIRE_INK.get(cut.colour, "#8c8c8c") if cut.insulated else _BARE_INK
    width = _INSULATED_MM if cut.insulated else _BARE_MM
    points = template.points
    line = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    holes = "".join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r=".5"/>' for x, y in template.holes)
    shapes = [
        f'<g fill="#d8d8d8">{holes}</g>',
        # A dark underlay, so a white or yellow wire still has an edge on white paper.
        f'<polyline points="{line}" fill="none" stroke="#555" stroke-width="{width + .3:.2f}" '
        'stroke-linejoin="round"/>',
        f'<polyline points="{line}" fill="none" stroke="{ink}" stroke-width="{width:.2f}" '
        'stroke-linejoin="round"/>',
    ]
    if template.strip_mm > 0:
        for tip, inner in ((points[0], points[1]), (points[-1], points[-2])):
            run = math.dist(tip, inner)
            k = template.strip_mm / run
            end = (tip[0] + (inner[0] - tip[0]) * k, tip[1] + (inner[1] - tip[1]) * k)
            shapes.append(
                f'<line x1="{tip[0]:.2f}" y1="{tip[1]:.2f}" x2="{end[0]:.2f}" y2="{end[1]:.2f}" '
                f'stroke="{_COPPER_INK}" stroke-width="{width + .35:.2f}"/>'
            )
    # A ring at every bend: the two end holes, where the legs go 90° down through the
    # board, and every corner, where the wire turns along it.
    for x, y in points[1:-1]:
        shapes.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.3" fill="none" stroke="#1667c8" '
            'stroke-width=".35"/>'
        )
    for hole, (x, y) in ((step.path[0], points[1]), (step.path[-1], points[-2])):
        name = format_hole(hole)
        # Beside the ring, on whichever side the template has room for it: a label run
        # off the edge of the drawing is a hole address with its last letter missing.
        right = x + 1.8 + len(name) * _LABEL_EM_MM <= template.width_mm
        anchor = "start" if right else "end"
        shapes.append(
            f'<text x="{x + 1.8 if right else max(x - 1.8, len(name) * _LABEL_EM_MM):.2f}" '
            f'y="{max(y - 1.6, 2.6):.2f}" '
            f'text-anchor="{anchor}" font-size="2.6" font-family="sans-serif" fill="#333">'
            f"{escape(name)}</text>"
        )
    return (
        f'<svg width="{template.width_mm * scale:.2f}mm" '
        f'height="{template.height_mm * scale:.2f}mm" '
        f'viewBox="0 0 {template.width_mm:.2f} {template.height_mm:.2f}">'
        + "".join(shapes)
        + "</svg>"
    )


#: The straightened wire's marks: blue where it bends, like the rings on the shape beside
#: it -- solid down through the board, dashed along it -- and dark where the insulation
#: is cut, which a mark in the strip's own copper colour would vanish into.
_MARK_INK = {"strip": "#333", "drop": "#1667c8", "turn": "#1667c8"}
_MARK_DASH = {"strip": "", "drop": "", "turn": ' stroke-dasharray=".8 .5"'}
#: The two label rows under the marks, in mm below the wire. Neighbouring marks alternate
#: rows: a corner one pitch from a bend puts two distances 2.54 mm apart, and one row of
#: "12.3" and "14.8" would print them on top of each other.
_TOTAL_ROWS_MM = (8.8, 11.6)
#: The same trouble above the wire: a piece one pitch long has room under its "2.5" for
#: about three characters, so a short piece's length goes up a row when the last one
#: did not.
_SHORT_PIECE_MM = 3.6


def _straight_svg(template: WireTemplate, step: ConductorStep, scale: float = 1) -> str:
    """The wire straightened, at 1:1: what is cut and marked BEFORE anything is bent.

    Above the wire, the distance from each mark to the next -- what to measure off
    with a ruler. Below it, each mark's distance from the left tip, so a whole wire can be
    marked from one end without adding up the errors of every step. A mark's ink says what
    happens there: blue is a bend, the copper colour is where the insulation is cut.
    """
    cut = step.cut
    assert cut is not None
    m = WIRE_TEMPLATE_MARGIN_MM
    length = template.length_mm
    width, height, y = length + 2 * m, 14.6, 6.0
    ink = _WIRE_INK.get(cut.colour, "#8c8c8c") if cut.insulated else _BARE_INK
    stroke = _INSULATED_MM if cut.insulated else _BARE_MM
    shapes = [
        f'<line x1="{m:.2f}" y1="{y}" x2="{m + length:.2f}" y2="{y}" stroke="#555" '
        f'stroke-width="{stroke + .3:.2f}"/>',
        f'<line x1="{m:.2f}" y1="{y}" x2="{m + length:.2f}" y2="{y}" stroke="{ink}" '
        f'stroke-width="{stroke:.2f}"/>',
    ]
    if template.strip_mm > 0:
        for start in (0.0, length - template.strip_mm):
            shapes.append(
                f'<line x1="{m + start:.2f}" y1="{y}" x2="{m + start + template.strip_mm:.2f}" '
                f'y2="{y}" stroke="{_COPPER_INK}" stroke-width="{stroke + .35:.2f}"/>'
            )
    text = 'font-family="sans-serif" fill="#333" text-anchor="middle"'
    stops = [0.0, *(mark.at_mm for mark in template.marks), length]
    raised = False
    for left, right in itertools.pairwise(stops):
        raised = right - left < _SHORT_PIECE_MM and not raised
        shapes.append(
            f'<text x="{m + (left + right) / 2:.2f}" y="{y - (3.8 if raised else 1.3):.2f}" '
            f'font-size="2.2" {text}>{right - left:.1f}</text>'
        )
    for index, mark in enumerate(template.marks):
        x = m + mark.at_mm
        row = _TOTAL_ROWS_MM[index % 2]
        shapes.append(
            f'<line x1="{x:.2f}" y1="{y - 1.2:.2f}" x2="{x:.2f}" y2="{row - 2.1:.2f}" '
            f'stroke="{_MARK_INK[mark.kind]}" stroke-width=".35"{_MARK_DASH[mark.kind]}/>'
            f'<text x="{x:.2f}" y="{row:.2f}" font-size="2" {text}>{mark.at_mm:.1f}</text>'
        )
    return (
        f'<svg width="{width * scale:.2f}mm" height="{height * scale:.2f}mm" '
        f'viewBox="0 0 {width:.2f} {height:.2f}">' + "".join(shapes) + "</svg>"
    )


def _template_pair(template: WireTemplate, step: ConductorStep, scale: float = 1) -> str:
    """The shape and the straightened wire, side by side where the page has room."""
    return (
        f'<div class="tpl-pair">{_template_svg(template, step, scale)}'
        f"{_straight_svg(template, step, scale)}</div>"
    )


#: The calibration bar's length, in mm. Long enough that a printer scaling to 97 % --
#: "fit to page", the usual default -- is 1.5 mm short and visible against a ruler.
RULER_MM = 50


def _ruler_svg() -> str:
    ticks = "".join(
        f'<line x1="{x}" y1="{2 if x % 10 else 0}" x2="{x}" y2="5" stroke="#000" '
        'stroke-width=".25"/>'
        for x in range(0, RULER_MM + 1, 5)
    )
    return (
        f'<svg class="ruler" width="{RULER_MM + 1}mm" '
        f'height="9mm" viewBox="-.5 0 {RULER_MM + 1} 9">'
        f'<line x1="0" y1="5" x2="{RULER_MM}" y2="5" stroke="#000" stroke-width=".35"/>'
        f'{ticks}<text x="0" y="8.6" font-size="3" font-family="sans-serif">'
        f"{RULER_MM} mm</text></svg>"
    )


def _html_templates(guide: Guide, say: Phrasebook) -> str:
    """The last pages: every wire at 1:1, to bend on.

    A screen has no millimetres -- CSS's ``mm`` is a fixed number of pixels that is right
    on no particular phone -- so the 1:1 promise is made only on PAPER, printed at 100 %,
    and the ruler is how the reader checks the printer kept it.
    """
    items: list[str] = []
    for step in all_steps(guide):
        if not isinstance(step, ConductorStep):
            continue
        template = wire_template(step, guide.board)
        if template is None or step.cut is None:
            continue
        side = say("component side") if template.seen_from == "top" else say("solder side")
        items.append(
            '<div class="tpl"><div class="title">'
            f"{escape(step.net_name)}: {_hole(step.path[0])} → {_hole(step.path[-1])}</div>"
            '<div class="meta">'
            # One short line: the sheet is a grid of these, and a caption that wraps to
            # three lines under a 20 mm drawing is most of the page.
            + say(
                "{length:.0f} mm · {colour} · AWG {awg} · {side}",
                length=step.cut.cut_mm,
                colour=escape(say(step.cut.colour)),
                awg=step.cut.awg,
                side=side,
            )
            + (" · " + say("turned a quarter") if template.turned else "")
            + f"</div>{_template_pair(template, step)}</div>"
        )
    if not items:
        return ""
    return (
        f'<section class="phase templates"><h2>{say("Wire templates (1:1)")}</h2>'
        "<p>"
        + say(
            "Print these pages at 100 % (actual size, not fit to page) and check the bar "
            "below against a ruler. Lay each wire on its drawing: cut it at the tips, strip "
            "the copper-coloured ends, bend it 90° down through the board at the two end "
            "rings and along the board at every other ring. Beside each one is the same "
            "wire straight, to mark before bending: above it the length of each piece, "
            "below it each mark's distance from the left tip. A solid blue mark bends down "
            "through the board, a dashed one along it, and a dark one is where to cut the "
            "insulation."
        )
        + f'</p>{_ruler_svg()}<div class="tpl-grid">{"".join(items)}</div></section>'
    )


def _html_tables(guide: Guide, say: Phrasebook) -> str:
    parts = [f'<section class="phase"><h2>{say("Lists")}</h2>']

    parts.append(f'<h3>{say("Parts")}</h3><div class="wrap"><table>')
    parts.append(
        f"<tr><th>{say('Qty')}</th><th>{say('Value')}</th><th>{say('Package')}</th>"
        f"<th>{say('References')}</th></tr>"
    )
    for line in guide.bom:
        parts.append(
            f"<tr><td>{line.quantity}</td><td>{escape(line.value)}</td>"
            f"<td>{escape(line.footprint_name)}</td><td>{escape(line.refs)}</td></tr>"
        )
    parts.append("</table></div>")

    if guide.cut_list or guide.spine_list:
        parts.append(f'<h3>{say("Wire")}</h3><div class="wrap"><table>')
        parts.append(
            "<tr>"
            + "".join(
                f"<th>{say(column)}</th>"
                for column in WIRE_TABLE_COLUMNS
            )
            + "</tr>"
        )
        # One spelling per thing, and it is the CSV's: the two tables are the same list
        # and somebody comparing them should not have to work out that "bare" here and
        # "bare wire" there are one row. The gauge belongs under AWG and the material
        # under Colour -- a spine's "0.6 mm tinned copper" used to sit under Colour with
        # an em dash under AWG, so the column headings said the opposite of the cells.
        for cut in guide.cut_list:
            parts.append(
                f"<tr><td>{_wire_type(cut.insulated, say)}</td>"
                f"<td>{escape(cut.net_name)}</td><td>{_hole(cut.from_hole)}</td>"
                f"<td>{_hole(cut.to_hole)}</td><td>{cut.cut_mm:.0f} mm</td>"
                f"<td>{cut.awg}</td><td>{escape(say(cut.colour))}</td></tr>"
            )
        for spine in guide.spine_list:
            parts.append(
                f"<tr><td>{say('trace spine')}</td><td>{escape(spine.net_name)}</td>"
                f"<td colspan=2>{say('{pads} pads', pads=spine.pads)}</td>"
                f"<td>{spine.length_mm:.0f} mm</td>"
                f"<td>{spine.gauge_mm:g} mm</td><td>{escape(say(spine.material))}</td></tr>"
            )
        parts.append("</table></div>")

    parts.append("</section>")
    return "".join(parts)


__all__ = [
    "BOM_COLUMNS",
    "CUT_LIST_COLUMNS",
    "RULER_MM",
    "WIRE_TABLE_COLUMNS",
    "bom_to_csv",
    "cut_list_to_csv",
    "guide_to_html",
    "guide_to_json",
]
