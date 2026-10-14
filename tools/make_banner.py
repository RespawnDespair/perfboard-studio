"""Draw the animated banner both READMEs open with.

    python tools/make_banner.py        # -> docs/images/banner.svg and banner.tr.svg

``tools/make_social_preview.py`` draws the card a link to the repository unfurls into from
the same code, as a still (``CARD``), so the two cannot drift apart.

WHAT IT SHOWS. The NE555 astable -- the circuit ``examples/ne555-astable.net`` imports to --
being built the way the build guide says to build it: the parts go in shortest first, the
board is turned over and the copper goes on, it is turned back and checked, and the LED
blinks. It is the one thing this application does that a picture can say in a few seconds,
and the story the assembly GIF further down the page tells in 3D.

NOTHING ON THE BOARD IS DRAWN BY HAND. The circuit is the example's own, on a board shaped
for a banner, placed by ``placer.place_design`` and routed by ``autoroute.plan_autoroute``
-- what Place on the Board and Ctrl+R call -- and the order is ``guide.all_steps``, so the
banner cannot show a build order the guide does not give. "It matches the schematic" is
said only because DRC and LVS were run on that board and said so: ``build_board`` refuses
to draw one they find fault with. The parts are drawn from
``ui/bodies.py`` (``BODY_STYLES``' colours, ``placement_for``'s real body rectangles,
``resistor_bands``' colour code), the board from ``ui/boardcolors.py`` and the copper from
the 2D view's own conductor table. Any of them changing shows here the next time this runs.

WHY CSS IN AN SVG. GitHub shows a README's images through ``<img>``, where an SVG may
animate but may not run a script; a CSS animation runs there in every browser GitHub
supports. Every element runs on ONE clock of ``period`` seconds, with its own timing written
into its own @keyframes as percentages of that clock, so the loop cannot drift out of step
with itself. A reader who has asked for reduced motion gets the finished board, lit.

THE MEDINSTECH WORDMARK IS INSIDE IT, as a PNG in a data URI: an SVG shown through ``<img>``
may not fetch another file, and the wordmark exists only as ``docs/images/wordmark-white.png``.
It is scaled to twice the size it is drawn at, which is sharp on a high-density screen and a
tenth of the bytes of the original.

THE WORDS ARE TEXT, NOT OUTLINES. A font cannot be fetched from inside an ``<img>``, and the
fonts that look right here are not ours to turn into outlines and ship. So the name and the
tagline are set in whatever sans-serif the reader's system has, and ``textLength`` holds each
line to the width it was designed at -- a wider fallback font is spaced tighter rather than
running the tagline into the board.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from perfboard_studio import persist  # noqa: E402
from perfboard_studio.autoroute import AutorouteOptions, plan_autoroute  # noqa: E402
from perfboard_studio.command import CommandBus, CommandContext  # noqa: E402
from perfboard_studio.commands import (  # noqa: E402
    UnplaceComponentPayload,
    create_document_id_generator,
    create_standard_registry,
)
from perfboard_studio.drc import run_drc  # noqa: E402
from perfboard_studio.footprints import footprint_lookup  # noqa: E402
from perfboard_studio.geometry import transform_offset  # noqa: E402
from perfboard_studio.guide import ConductorStep, PartStep, all_steps, build_guide  # noqa: E402
from perfboard_studio.lvs import run_lvs  # noqa: E402
from perfboard_studio.model import ComponentInstance, Footprint, PerfDocument  # noqa: E402
from perfboard_studio.placer import PlacementOptions, place_design  # noqa: E402
from perfboard_studio.router import RouterOptions, options_for_style  # noqa: E402
from perfboard_studio.ui import theme  # noqa: E402
from perfboard_studio.ui.boardcolors import SCHEMES  # noqa: E402
from perfboard_studio.ui.bodies import placement_for, resistor_bands, style_for  # noqa: E402
from perfboard_studio.ui.view2d import (  # noqa: E402
    _INSULATION_SCREEN,
    CONDUCTOR_STYLE,
    SPINE_CORE,
)

EXAMPLE = REPO_ROOT / "examples" / "ne555-astable.perf"
OUT_DIR = REPO_ROOT / "docs" / "images"

#: The board the circuit is laid on: wider than it is tall, to sit beside the name. The
#: example's own board is the 2 x 8 cm strip the Board step recommends, which on its side is
#: a ruler with parts on it; and on a board much bigger than this the placer, rightly, leaves
#: half of it bare.
BOARD_COLS, BOARD_ROWS = 14, 7

WORDMARK = REPO_ROOT / "docs" / "images" / "wordmark-white.png"

COPPER = "#c8a06a"  # the social preview's second rule colour: the board's copper.
FONT_STACK = (
    "'Segoe UI Variable Display','Segoe UI',-apple-system,BlinkMacSystemFont,"
    "'Helvetica Neue','Noto Sans','DejaVu Sans',Arial,sans-serif"
)
#: Set as an inline style where it is used: the stylesheet's `text` rule outranks a presentation
#: attribute, and quietly set the IC's marking and the card's last line in the sans-serif.
MONO_STACK = "Consolas,'DejaVu Sans Mono',Menlo,monospace"

# -- the clock ------------------------------------------------------------------------------

BOARD_IN_S = 0.5
FIRST_STEP_S = 0.8
PART_EVERY_S = 0.42
DROP_S = 0.38
#: The copper is laid in this long however many conductors there are, so a board routed
#: differently next time keeps the same rhythm.
COPPER_SPAN_S = 3.2
FLIP_S = 0.7
BLINKS, BLINK_PERIOD_S = 4, 1.0
FADE_S = 0.6


@dataclass(frozen=True)
class Layout:
    """Where everything goes on one picture, in that picture's own pixels."""

    width: int
    height: int
    #: Pixels per hole. Everything on the board is millimetres scaled by this over the pitch,
    #: so a 5 mm LED is as much bigger than a pad as it is in your hand.
    pitch_px: float
    board_x: float
    board_y: float
    text_x: float
    logo_y: float
    logo_h: float
    title_y: float
    title_size: float
    rule_y: float
    tagline_y: float
    tagline_size: float
    tagline_step: float
    keywords_y: float
    keywords_size: float
    corner: float
    #: Played, or shown finished and lit: the README's banner moves, a link's card is a still.
    animated: bool
    #: The card's last line, under a rule of its own: where to get it.
    footer: str | None = None
    footer_y: float = 0.0
    footer_size: float = 0.0


#: The README's: the board at the right, its top level with the wordmark's, and the bottom
#: line of words on the caption's baseline.
BANNER = Layout(
    width=1280,
    height=400,
    pitch_px=36.0,
    board_x=1280 - 72 - BOARD_COLS * 36.0,
    board_y=46.0,
    text_x=72.0,
    logo_y=46.0,
    logo_h=62.0,
    title_y=194.0,
    title_size=58,
    rule_y=210.0,
    tagline_y=256.0,
    tagline_size=26,
    tagline_step=34.0,
    keywords_y=362.0,
    keywords_size=15,
    corner=22.0,
    animated=True,
)

#: The card GitHub shows where the repository is linked: 1280 x 640, the size it asks for and
#: the 2:1 every unfurl crops towards. Read small, so everything is bigger, and the board
#: sits below the name rather than beside it to be bigger still.
CARD = Layout(
    width=1280,
    height=640,
    pitch_px=40.0,
    board_x=1280 - 72 - BOARD_COLS * 40.0,
    board_y=640 - 76 - BOARD_ROWS * 40.0,
    text_x=72.0,
    logo_y=64.0,
    logo_h=78.0,
    title_y=250.0,
    title_size=86,
    rule_y=274.0,
    tagline_y=340.0,
    tagline_size=34,
    tagline_step=44.0,
    keywords_y=478.0,
    keywords_size=22,
    corner=0.0,
    animated=False,
    footer="Get it from the releases page",
    footer_y=548.0,
    footer_size=22,
)


@dataclass(frozen=True)
class Words:
    tagline: tuple[str, str]
    keywords: str
    #: What the board is doing, one line per stretch of the build: the parts, the copper,
    #: the check, and the LED.
    captions: tuple[str, str, str, str]
    title: str


WORDS = {
    "en": Words(
        tagline=("Perfboard circuit design", "and soldering guides"),
        keywords="Autorouter · perfboard DRC · LVS · MCP server",
        captions=(
            "Parts go in, shortest first",
            "Turned over: the copper goes on",
            "Turned back: it matches the schematic",
            "Powered: the 555 blinks its LED",
        ),
        title="Perfboard Studio: the NE555 astable being built in the order its guide gives",
    ),
    "tr": Words(
        tagline=("Delikli plaket üzerinde devre tasarımı", "ve lehimleme rehberi"),
        keywords="Otomatik yönlendirici · perfboard DRC · LVS · MCP sunucusu",
        captions=(
            "Parçalar takılıyor, en kısadan başlayarak",
            "Kart çevrildi: bakır işleniyor",
            "Geri çevrildi: şemayla birebir örtüşüyor",
            "Güç verildi: 555 LED'i yakıp söndürüyor",
        ),
        title="Perfboard Studio: NE555 astable, rehberinin verdiği sırayla kuruluyor",
    ),
}


# ---------------------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------------------


def _bus(document: PerfDocument) -> CommandBus:
    return CommandBus(
        document,
        create_standard_registry(),
        CommandContext(next_id=create_document_id_generator(document)),
    )


def build_board() -> PerfDocument:
    """The example's circuit placed and routed on the banner's board, by the engine.

    The parts are taken back into the design and put down again by ``place_design``, as
    Place on the Board puts them, and the copper is the solder-first plan with wires on the
    grid -- solder traces are what read as perfboard from across a room. Then DRC and LVS,
    because the banner says the board matches its schematic.
    """
    result = persist.deserialize_document(EXAMPLE.read_text(encoding="utf-8"))
    if not result.ok:
        raise SystemExit(f"LOAD FAILED [{result.code}] {result.message}")
    example = result.document
    board = dataclasses.replace(
        example.board, cols=BOARD_COLS, rows=BOARD_ROWS, border_x_mm=0, border_y_mm=0, labels=None
    )
    document = dataclasses.replace(
        example, board=board, conductors=(), mounting_holes=(), edge_connectors=(), board_notes=()
    )
    bus = _bus(document)
    for component in document.components:
        taken = bus.dispatch("component.unplace", UnplaceComponentPayload(id=component.id))
        if not taken.ok:
            raise SystemExit(f"UNPLACE REFUSED [{taken.code}] {taken.message}")

    lookup = footprint_lookup()
    placed = place_design(bus.document, lookup, PlacementOptions(seed=0))
    if placed.unplaced:
        raise SystemExit(f"NOT PLACED: {', '.join(placed.unplaced)}")
    router = options_for_style("solder", RouterOptions(wire_path="grid"))
    plan = plan_autoroute(placed.document, lookup, AutorouteOptions(router=router))
    if plan.summary.links_unrouted:
        raise SystemExit(f"{plan.summary.links_unrouted} connection(s) left unrouted")
    bus = _bus(placed.document)
    routed = bus.dispatch("conductor.addMany", plan.payload())
    if not routed.ok:
        raise SystemExit(f"ROUTE REFUSED [{routed.code}] {routed.message}")
    errors = [v for v in run_drc(bus.document, lookup) if v.severity == "error"]
    if errors:
        raise SystemExit(f"DRC: {len(errors)} error(s), first {errors[0].rule}: {errors[0].message}")
    if not run_lvs(bus.document, lookup).ok:
        raise SystemExit("LVS: the board does not match its schematic")
    return bus.document


# ---------------------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------------------


def num(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


class Canvas:
    """Millimetres on the board to pixels in the banner, for either face.

    The back is the front turned over about the board's long axis -- the way it is turned
    on the bench -- so a hole's row is mirrored and its column is not.
    """

    def __init__(self, document: PerfDocument, layout: Layout) -> None:
        self.board = document.board
        self.pitch = document.board.pitch
        self.x, self.y, self.pitch_px = layout.board_x, layout.board_y, layout.pitch_px
        self.scale = self.pitch_px / self.pitch

    def mm(self, x_mm: float, y_mm: float, back: bool = False) -> tuple[float, float]:
        x = self.x + self.pitch_px / 2 + x_mm * self.scale
        if back:
            y_mm = (self.board.rows - 1) * self.pitch - y_mm
        return x, self.y + self.pitch_px / 2 + y_mm * self.scale

    def hole(self, col: int, row: int, back: bool = False) -> tuple[float, float]:
        return self.mm(col * self.pitch, row * self.pitch, back)

    @property
    def size(self) -> tuple[float, float]:
        return self.board.cols * self.pitch_px, self.board.rows * self.pitch_px

    @property
    def centre(self) -> tuple[float, float]:
        width, height = self.size
        return self.x + width / 2, self.y + height / 2


@dataclass(frozen=True)
class Body:
    """A part's body on the front face: centre and size in pixels, and which way its leads
    run -- ``placement_for``'s rectangle with the component's own turn applied."""

    x: float
    y: float
    w: float
    h: float
    axis: str

    @property
    def length(self) -> float:
        return self.w if self.axis == "x" else self.h

    @property
    def across(self) -> float:
        return self.h if self.axis == "x" else self.w


def body_of(canvas: Canvas, component: ComponentInstance, footprint: Footprint) -> Body:
    place = placement_for(footprint, canvas.pitch)
    dx, dy = transform_offset(place.centre_x, place.centre_y, component.rotation, component.mirrored)
    x, y = canvas.mm(component.anchor.col * canvas.pitch + dx, component.anchor.row * canvas.pitch + dy)
    turned = component.rotation in (90, 270)
    w, h = (place.size_y, place.size_x) if turned else (place.size_x, place.size_y)
    axis = place.axis if not turned else ("y" if place.axis == "x" else "x")
    return Body(x, y, w * canvas.scale, h * canvas.scale, axis)


def _shade(body: Body) -> str:
    """The light across a round body: the gradient runs across its axis."""
    return "url(#shade-x)" if body.axis == "x" else "url(#shade-y)"


def draw_part(canvas: Canvas, step: PartStep, component: ComponentInstance, footprint: Footprint) -> str:
    style = style_for(footprint)
    body = body_of(canvas, component, footprint)
    pins = {number: canvas.hole(hole.col, hole.row) for number, hole in step.pin_holes}
    names = {pin.number: pin.name for pin in footprint.pins}
    archetype = footprint.body.archetype
    out: list[str] = []
    s = canvas.scale

    def lead_between() -> None:
        (x1, y1), (x2, y2) = list(pins.values())[:2]
        out.append(
            f'<path d="M{num(x1)} {num(y1)}L{num(x2)} {num(y2)}" stroke="#b7bcc4" '
            f'stroke-width="{num(0.55 * s)}" stroke-linecap="round"/>'
        )

    def rect(fill: str, rx: float, extra: str = "") -> str:
        return (
            f'<rect x="{num(body.x - body.w / 2)}" y="{num(body.y - body.h / 2)}" '
            f'width="{num(body.w)}" height="{num(body.h)}" rx="{num(rx)}" fill="{fill}"{extra}/>'
        )

    if archetype in ("axial-cylinder", "disc-ceramic"):
        lead_between()
        rx = body.across / 2 if archetype == "axial-cylinder" else body.across * 0.45
        out.append(rect(style.fill, rx, f' stroke="{style.edge}" stroke-width="1.2"'))
        bands = resistor_bands(footprint, step.value)
        if bands:
            # Three bands from the end pin 1 is at and the tolerance band apart at the
            # other, as a resistor is printed.
            first = pins[step.pin_holes[0][0]]
            sign = -1 if (first[0] < body.x if body.axis == "x" else first[1] < body.y) else 1
            for colour, at in zip(bands, (-0.30, -0.13, 0.04, 0.30), strict=False):
                offset = sign * at * body.length
                band = body.length * 0.085
                if body.axis == "x":
                    out.append(
                        f'<rect x="{num(body.x + offset - band / 2)}" y="{num(body.y - body.h / 2)}" '
                        f'width="{num(band)}" height="{num(body.h)}" fill="{colour}"/>'
                    )
                else:
                    out.append(
                        f'<rect x="{num(body.x - body.w / 2)}" y="{num(body.y + offset - band / 2)}" '
                        f'width="{num(body.w)}" height="{num(band)}" fill="{colour}"/>'
                    )
        out.append(rect(_shade(body), rx))
    elif archetype == "radial-electrolytic":
        r = min(body.w, body.h) / 2
        minus = next((pins[n] for n, name in names.items() if name == "-" and n in pins), None)
        clip = f"clip-{step.ref.lower()}"
        out.append(
            f'<circle cx="{num(body.x)}" cy="{num(body.y)}" r="{num(r)}" fill="{style.fill}" '
            f'stroke="{style.edge}" stroke-width="1.2"/>'
        )
        if minus is not None:
            # The printed stripe marks the NEGATIVE lead: the side of the can beyond a chord
            # facing the '-' pin.
            dx, dy = minus[0] - body.x, minus[1] - body.y
            length = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux, uy = dx / length, dy / length
            near = r * 0.52
            corners = [
                (body.x + ux * near - uy * r * 2, body.y + uy * near + ux * r * 2),
                (body.x + ux * r * 2 - uy * r * 2, body.y + uy * r * 2 + ux * r * 2),
                (body.x + ux * r * 2 + uy * r * 2, body.y + uy * r * 2 - ux * r * 2),
                (body.x + ux * near + uy * r * 2, body.y + uy * near - ux * r * 2),
            ]
            points = " ".join(f"{num(x)},{num(y)}" for x, y in corners)
            out.append(
                f'<clipPath id="{clip}"><circle cx="{num(body.x)}" cy="{num(body.y)}" r="{num(r)}"/></clipPath>'
                f'<polygon points="{points}" fill="{style.accent}" opacity="0.7" clip-path="url(#{clip})"/>'
            )
        # The vent stamped in the top of the can.
        v = r * 0.42
        out.append(
            f'<path d="M{num(body.x - v)} {num(body.y)}h{num(2 * v)}M{num(body.x)} {num(body.y - v)}v{num(2 * v)}" '
            f'stroke="#0b1120" stroke-width="1.6" opacity="0.7"/>'
            f'<circle cx="{num(body.x)}" cy="{num(body.y)}" r="{num(r)}" fill="url(#dome)"/>'
        )
    elif archetype == "led-round":
        r = min(body.w, body.h) / 2
        cathode = next((pins[n] for n, name in names.items() if name == "K" and n in pins), None)
        clip = f"clip-{step.ref.lower()}"
        flat = ""
        if cathode is not None:
            # The flat on the rim is on the cathode side: a half-plane clips the circle.
            dx, dy = cathode[0] - body.x, cathode[1] - body.y
            length = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux, uy = dx / length, dy / length
            cut = r * 0.84
            corners = [
                (body.x - ux * r * 2 - uy * r * 2, body.y - uy * r * 2 + ux * r * 2),
                (body.x + ux * cut - uy * r * 2, body.y + uy * cut + ux * r * 2),
                (body.x + ux * cut + uy * r * 2, body.y + uy * cut - ux * r * 2),
                (body.x - ux * r * 2 + uy * r * 2, body.y - uy * r * 2 - ux * r * 2),
            ]
            points = " ".join(f"{num(x)},{num(y)}" for x, y in corners)
            out.append(f'<clipPath id="{clip}"><polygon points="{points}"/></clipPath>')
            flat = f' clip-path="url(#{clip})"'
        out.append(
            f'<g{flat}><circle cx="{num(body.x)}" cy="{num(body.y)}" r="{num(r)}" fill="{style.fill}" '
            f'stroke="{style.edge}" stroke-width="1.6"/>'
            f'<circle class="a" id="lit" cx="{num(body.x)}" cy="{num(body.y)}" r="{num(r)}" fill="url(#lit-lens)"/>'
            f'<circle cx="{num(body.x)}" cy="{num(body.y)}" r="{num(r * 0.62)}" fill="none" '
            f'stroke="{style.accent}" stroke-width="1.2" opacity="0.35"/></g>'
            f'<ellipse cx="{num(body.x - r * 0.32)}" cy="{num(body.y - r * 0.36)}" rx="{num(r * 0.3)}" '
            f'ry="{num(r * 0.2)}" fill="#fff" opacity="0.55"/>'
        )
    elif archetype in ("pin-header", "box-header"):
        out.append(rect(style.fill, 2.5, f' stroke="{style.edge}" stroke-width="1.2"'))
        half = 0.32 * s
        for x, y in pins.values():
            out.append(
                f'<rect x="{num(x - half)}" y="{num(y - half)}" width="{num(2 * half)}" '
                f'height="{num(2 * half)}" rx="1" fill="{style.accent}"/>'
                f'<rect x="{num(x - half * 0.45)}" y="{num(y - half * 0.45)}" width="{num(half * 0.9)}" '
                f'height="{num(half * 0.9)}" fill="#f2dc9a" opacity="0.7"/>'
            )
    else:
        # A DIP, and anything else: the case, its legs out to the pins, and pin 1 marked.
        for x, y in pins.values():
            if body.axis == "x":
                edge = body.y - body.h / 2 if y < body.y else body.y + body.h / 2
                out.append(
                    f'<rect x="{num(x - 0.3 * s)}" y="{num(min(y, edge))}" width="{num(0.6 * s)}" '
                    f'height="{num(abs(edge - y))}" fill="#c3c8d2"/>'
                )
            else:
                edge = body.x - body.w / 2 if x < body.x else body.x + body.w / 2
                out.append(
                    f'<rect x="{num(min(x, edge))}" y="{num(y - 0.3 * s)}" width="{num(abs(edge - x))}" '
                    f'height="{num(0.6 * s)}" fill="#c3c8d2"/>'
                )
        out.append(rect(style.fill, 3, f' stroke="{style.edge}" stroke-width="1.2"'))
        out.append(rect("url(#case)", 3))
        first = pins.get("1")
        if first is not None:
            # The notch is a half-moon cut INTO the pin-1 end, centred across the case, and
            # the dot sits inside the corner pin 1 is at. Arc sweeps: 1 is clockwise on a
            # y-down screen, so each end's flag is the one that bulges into the case.
            r = 0.75 * s
            if body.axis == "x":
                left = first[0] < body.x
                end = body.x - body.w / 2 if left else body.x + body.w / 2
                notch = f"M{num(end)} {num(body.y - r)}A{num(r)} {num(r)} 0 0 {1 if left else 0} {num(end)} {num(body.y + r)}Z"
                dot = (end + (1.5 if left else -1.5) * s, first[1] + (1.0 if first[1] < body.y else -1.0) * s)
            else:
                top = first[1] < body.y
                end = body.y - body.h / 2 if top else body.y + body.h / 2
                notch = f"M{num(body.x - r)} {num(end)}A{num(r)} {num(r)} 0 0 {0 if top else 1} {num(body.x + r)} {num(end)}Z"
                dot = (first[0] + (1.0 if first[0] < body.x else -1.0) * s, end + (1.5 if top else -1.5) * s)
            out.append(
                f'<path d="{notch}" fill="#141519"/>'
                f'<circle cx="{num(dot[0])}" cy="{num(dot[1])}" r="{num(0.36 * s)}" fill="#17181c" '
                f'stroke="#3a3d46" stroke-width="1"/>'
            )
        angle = 0 if body.axis == "x" else -90
        out.append(
            f'<text x="{num(body.x)}" y="{num(body.y)}" transform="rotate({angle} {num(body.x)} {num(body.y)})" '
            f'style="font-family:{MONO_STACK}" font-size="{num(0.95 * s)}" '
            f'fill="#8d93a0" text-anchor="middle" dominant-baseline="central" letter-spacing="1">'
            f"{escape(step.value)}</text>"
        )
    return "".join(out)


def conductor_strokes(step: ConductorStep, canvas: Canvas) -> list[tuple[str, float, float]]:
    """(colour, width, opacity) for each pass of a conductor's pen, widest first: the 2D
    view's own colours and widths, with a sleeve's darker edge and a sheen on the metal."""
    s = canvas.scale
    base, width_mm, _dashed = CONDUCTOR_STYLE[step.conductor_kind]
    colour = base.name()
    if step.conductor_kind == "insulated-wire" and step.cut is not None:
        named = _INSULATION_SCREEN.get(step.cut.colour)
        colour = named.name() if named is not None else colour
        return [("#0c0e12", (width_mm + 0.35) * s, 0.55), (colour, width_mm * s, 1.0), ("#ffffff", 0.22 * s, 0.28)]
    if step.conductor_kind == "solder-trace-wired":
        return [(colour, width_mm * s, 1.0), (SPINE_CORE.name(), 0.3 * s, 0.9)]
    return [(colour, width_mm * s, 1.0), ("#f1f3f6", 0.26 * s, 0.45)]


# ---------------------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------------------


class Clock:
    """One loop of the animation, and the @keyframes every element runs on it."""

    def __init__(self, period: float) -> None:
        self.period = period
        self.rules: list[str] = []

    def at(self, seconds: float) -> str:
        return f"{100 * min(max(seconds, 0.0), self.period) / self.period:.3f}%"

    def animate(self, selector: str, name: str, frames: Iterable[tuple[float, str]]) -> None:
        """``frames`` is (seconds, declarations); the first is held from 0 and the last
        to the end of the loop, so an element is exactly where it should be on every lap."""
        frames = sorted(frames, key=lambda f: f[0])
        stops = [(0.0, frames[0][1]), *frames, (self.period, frames[-1][1])]
        body = "".join(f"{self.at(t)}{{{decl}}}" for t, decl in stops)
        self.rules.append(f"@keyframes {name}{{{body}}}{selector}{{animation-name:{name}}}")


EASE_OUT = "animation-timing-function:cubic-bezier(.2,.8,.25,1)"
EASE_IN = "animation-timing-function:cubic-bezier(.55,0,.8,.3)"


# ---------------------------------------------------------------------------------------
# The banner
# ---------------------------------------------------------------------------------------


def text_width(text: str, size: float, bold: bool) -> float:
    """How wide a line was designed to be: measured in the first of the stack's fonts this
    machine has, and handed to ``textLength`` so every reader's font is held to it."""
    from PIL import ImageFont

    names = ("segoeuib.ttf", "DejaVuSans-Bold.ttf") if bold else ("segoeui.ttf", "DejaVuSans.ttf")
    for name in names:
        try:
            return float(ImageFont.truetype(name, round(size)).getlength(text))
        except OSError:
            continue
    raise SystemExit("no Segoe UI or DejaVu Sans to measure the words with")


def wordmark(height: float) -> tuple[float, str]:
    """The Medinstech wordmark as a data URI at twice ``height``, and its width at ``height``."""
    from PIL import Image

    with Image.open(WORDMARK) as source:
        width = height * source.width / source.height
        small = source.resize((round(2 * width), round(2 * height)), Image.LANCZOS)
    data = io.BytesIO()
    small.save(data, format="PNG", optimize=True)
    return width, "data:image/png;base64," + base64.b64encode(data.getvalue()).decode("ascii")


def render(document: PerfDocument, words: Words, layout: Layout) -> str:
    lookup = footprint_lookup()
    canvas = Canvas(document, layout)
    steps = all_steps(build_guide(document, lookup))
    components = {c.id: c for c in document.components}
    scheme = next(s for s in SCHEMES if s.key == "green")

    # Which face each step is done on, in order, and where the board turns over between.
    faces = ["back" if isinstance(st, ConductorStep) and st.side == "bottom" else "front" for st in steps]
    runs = [face for face, _ in groupby(faces)]
    if runs != ["front", "back"]:
        # The captions say "the parts, then the copper". A guide that orders this board some
        # other way needs other words, not these ones printed over it.
        raise SystemExit(f"the build runs {runs}, not the parts and then the copper")

    copper = [st for st in steps if isinstance(st, ConductorStep)]
    copper_every = COPPER_SPAN_S / max(len(copper), 1)
    draw_s = max(0.35, copper_every * 2.5)

    t = FIRST_STEP_S
    face = "front"
    flips: list[float] = []
    starts: list[float] = []
    for st, f in zip(steps, faces, strict=True):
        if f != face:
            flips.append(t)
            t += FLIP_S + 0.15
            face = f
        starts.append(t)
        t += PART_EVERY_S if isinstance(st, PartStep) else copper_every
    # Turned back over once the copper is on, to be checked and switched on part side up.
    flips.append(t + draw_s - copper_every)
    built = flips[-1] + FLIP_S
    blink_from = built + 0.45
    blink_to = blink_from + BLINKS * BLINK_PERIOD_S
    fade_from = blink_to + 0.2
    clock = Clock(fade_from + FADE_S + 0.4)

    cx, cy = canvas.centre
    board_w, board_h = canvas.size
    half_flip = FLIP_S / 2
    parts_front: list[str] = []
    copper_svg: list[str] = []
    fillets: list[str] = []

    for index, (st, start) in enumerate(zip(steps, starts, strict=True)):
        if isinstance(st, PartStep):
            component = components[st.component_id]
            footprint = lookup(component.footprint_id)
            assert footprint is not None
            ident = f"p{index}"
            group = f'<g class="a part" id="{ident}" filter="url(#lift)">{draw_part(canvas, st, component, footprint)}</g>'
            parts_front.append(group)
            clock.animate(
                f"#{ident}",
                ident,
                [
                    (start, f"opacity:0;transform:translate(0px,-16px) scale(1.14);{EASE_OUT}"),
                    (start + DROP_S, "opacity:1;transform:translate(0px,0px) scale(1)"),
                ],
            )
            if start < flips[0]:
                # Soldered before the board is turned over, so its joints are there when it is.
                for _number, hole in st.pin_holes:
                    x, y = canvas.hole(hole.col, hole.row, back=True)
                    fillets.append(
                        f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(0.78 * canvas.scale)}" fill="url(#fillet)"/>'
                        f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(0.26 * canvas.scale)}" fill="#eef0f3"/>'
                    )
        else:
            ident = f"c{index}"
            points = [canvas.hole(h.col, h.row, back=True) for h in st.path]
            d = "M" + "L".join(f"{num(x)} {num(y)}" for x, y in points)
            for colour, width, opacity in conductor_strokes(st, canvas):
                copper_svg.append(
                    f'<path class="a {ident}" d="{d}" pathLength="1" stroke="{colour}" '
                    f'stroke-width="{num(width)}" opacity="{opacity}"/>'
                )
            clock.animate(
                f".{ident}",
                ident,
                [
                    (start - 0.01, "stroke-dashoffset:1;visibility:hidden"),
                    (start, "stroke-dashoffset:1;visibility:visible;animation-timing-function:ease-in-out"),
                    (start + draw_s, "stroke-dashoffset:0;visibility:visible"),
                ],
            )

    to_back, to_front = flips
    clock.animate(
        "#front",
        "front",
        [
            (to_back, f"transform:scaleY(1);{EASE_IN}"),
            (to_back + half_flip, "transform:scaleY(0.001)"),
            (to_front + half_flip, f"transform:scaleY(0.001);{EASE_OUT}"),
            (to_front + FLIP_S, "transform:scaleY(1)"),
        ],
    )
    clock.animate(
        "#back",
        "back",
        [
            (to_back + half_flip, f"opacity:1;transform:scaleY(0.001);{EASE_OUT}"),
            (to_back + FLIP_S, "opacity:1;transform:scaleY(1)"),
            (to_front, f"opacity:1;transform:scaleY(1);{EASE_IN}"),
            (to_front + half_flip, "opacity:1;transform:scaleY(0.001)"),
        ],
    )
    clock.animate(
        "#board",
        "board",
        [
            (0.0, f"opacity:0;transform:translate(0px,10px);{EASE_OUT}"),
            (BOARD_IN_S, "opacity:1;transform:translate(0px,0px)"),
            (fade_from, "opacity:1;transform:translate(0px,0px)"),
            (fade_from + FADE_S, "opacity:0;transform:translate(0px,0px)"),
        ],
    )
    # The build's progress, as the 3D view's assembly slider shows it.
    clock.animate(
        "#progress",
        "progress",
        [(FIRST_STEP_S - 0.2, "transform:scaleX(0)"), (built, "transform:scaleX(1)")],
    )
    blink: list[tuple[float, str]] = [(blink_from - 0.01, "opacity:0")]
    for n in range(BLINKS):
        on = blink_from + n * BLINK_PERIOD_S
        blink += [
            (on, "opacity:0;animation-timing-function:ease-out"),
            (on + 0.08, "opacity:1"),
            (on + BLINK_PERIOD_S / 2, "opacity:1;animation-timing-function:ease-in"),
            (on + BLINK_PERIOD_S / 2 + 0.12, "opacity:0"),
        ]
    clock.animate("#lit, #glow", "blink", blink)

    spans = [
        (FIRST_STEP_S - 0.3, to_back),
        (to_back + FLIP_S, to_front),
        (to_front + FLIP_S, blink_from - 0.1),
        (blink_from, fade_from + FADE_S * 0.5),
    ]
    captions: list[str] = []
    for n, (caption, (start, end)) in enumerate(zip(words.captions, spans, strict=True)):
        ident = f"say{n}"
        captions.append(
            f'<text class="a caption" id="{ident}" x="{num(cx)}" y="{num(layout.board_y + board_h + 64)}" '
            f'text-anchor="middle">{escape(caption)}</text>'
        )
        clock.animate(
            f"#{ident}",
            ident,
            [
                (start - 0.01, "opacity:0;transform:translate(0px,4px)"),
                (start + 0.25, "opacity:1;transform:translate(0px,0px)"),
                (end - 0.25, "opacity:1;transform:translate(0px,0px)"),
                (end, "opacity:0;transform:translate(0px,-4px)"),
            ],
        )

    # -- the words ------------------------------------------------------------------------
    x0 = layout.text_x
    title_w = text_width("Perfboard Studio", layout.title_size, bold=True)
    tag_ws = [text_width(line, layout.tagline_size, bold=False) for line in words.tagline]
    key_w = text_width(words.keywords, layout.keywords_size, bold=False)
    lines = [
        (title_w, layout.title_y),
        *((w, layout.tagline_y + n * layout.tagline_step) for n, w in enumerate(tag_ws)),
        (key_w, layout.keywords_y),
    ]
    for line_w, baseline in lines:
        # A line beside the board must stop short of it; one above it may run on.
        if baseline > layout.board_y - 10 and x0 + line_w > layout.board_x - 40:
            raise SystemExit(f"a line runs to {x0 + line_w:.0f} px; the board starts at {layout.board_x:.0f}")
    logo_w, logo = wordmark(layout.logo_h)
    rule_long, rule_short = layout.title_size * 1.34, layout.title_size * 0.9

    words_svg = [
        f'<a href="https://medinstech.com"><image x="{num(x0)}" y="{num(layout.logo_y)}" '
        f'width="{num(logo_w)}" height="{num(layout.logo_h)}" href="{logo}"/></a>',
        f'<text x="{num(x0)}" y="{num(layout.title_y)}" font-size="{num(layout.title_size)}" '
        f'font-weight="700" fill="{theme.TEXT}" textLength="{num(title_w)}" '
        f'lengthAdjust="spacing">Perfboard Studio</text>',
        # The rule in the two colours the product is made of: the interface's accent and
        # the board's copper.
        f'<rect x="{num(x0 + 2)}" y="{num(layout.rule_y)}" width="{num(rule_long)}" height="5" '
        f'rx="1" fill="{theme.ACCENT}"/>'
        f'<rect x="{num(x0 + 10 + rule_long)}" y="{num(layout.rule_y)}" width="{num(rule_short)}" '
        f'height="5" rx="1" fill="{COPPER}"/>',
    ]
    for n, (line, line_w) in enumerate(zip(words.tagline, tag_ws, strict=True)):
        words_svg.append(
            f'<text x="{num(x0)}" y="{num(layout.tagline_y + n * layout.tagline_step)}" '
            f'font-size="{num(layout.tagline_size)}" fill="{theme.TEXT_DIM}" '
            f'textLength="{num(line_w)}" lengthAdjust="spacing">{escape(line)}</text>'
        )
    words_svg.append(
        f'<text x="{num(x0)}" y="{num(layout.keywords_y)}" font-size="{num(layout.keywords_size)}" '
        f'fill="{theme.TEXT_DIM}" opacity=".75" textLength="{num(key_w)}" lengthAdjust="spacing">'
        f"{escape(words.keywords)}</text>"
    )
    if layout.footer:
        divider_y = layout.footer_y - layout.footer_size * 1.9
        words_svg.append(
            f'<rect x="{num(x0)}" y="{num(divider_y)}" width="{num(key_w * 0.8)}" height="2" '
            f'fill="{theme.BORDER}"/>'
            f'<text x="{num(x0)}" y="{num(layout.footer_y)}" font-size="{num(layout.footer_size)}" '
            f'style="font-family:{MONO_STACK}" fill="{theme.ACCENT}">{escape(layout.footer)}</text>'
        )

    led = next(
        body_of(canvas, components[st.component_id], lookup(components[st.component_id].footprint_id))  # type: ignore[arg-type]
        for st in steps
        if isinstance(st, PartStep) and st.archetype == "led-round"
    )

    # -- the picture --------------------------------------------------------------------
    pad_r = canvas.board.pad_diameter / 2 * canvas.scale
    hole_r = canvas.board.drill_diameter / 2 * canvas.scale
    p = layout.pitch_px
    bx, by = layout.board_x, layout.board_y
    board_rect = f'x="{num(bx)}" y="{num(by)}" width="{num(board_w)}" height="{num(board_h)}" rx="7"'
    width, height, corner = layout.width, layout.height, layout.corner

    def face_svg(ident: str, inner: str) -> str:
        return (
            f'<g class="a" id="{ident}">'
            # Its shadow and its edge turn over with it: left behind, the shadow is a dark
            # band where the board was.
            f'<rect x="{num(bx + 10)}" y="{num(by + 22)}" width="{num(board_w - 20)}" '
            f'height="{num(board_h)}" rx="18" fill="#000" opacity=".55" filter="url(#soft)"/>'
            f'<rect x="{num(bx)}" y="{num(by + 5)}" width="{num(board_w)}" height="{num(board_h)}" '
            f'rx="7" fill="{scheme.edge}"/>'
            f'<rect {board_rect} fill="{scheme.fill}"/>'
            f'<rect {board_rect} fill="url(#pads)"/>'
            f'<rect {board_rect} fill="url(#sheen)" stroke="{scheme.edge}" stroke-width="1.5"/>'
            f"{inner}</g>"
        )

    if layout.animated:
        motion = f"""
.a{{animation-duration:{clock.period:.3f}s;animation-iteration-count:infinite;animation-timing-function:linear}}
.part,.caption{{transform-box:fill-box;transform-origin:center}}
#front,#back{{transform-box:view-box;transform-origin:{num(cx)}px {num(cy)}px}}
#progress{{transform-box:fill-box;transform-origin:left center}}
#say0,#say1,#say2{{opacity:0}}
.caption{{font-size:15px;fill:{theme.TEXT_DIM};letter-spacing:.02em}}
{"".join(clock.rules)}
@media (prefers-reduced-motion:reduce){{.a{{animation:none!important}}}}"""
        # The build's progress, as the 3D view's assembly slider shows it, and what it is
        # doing in words.
        under = (
            f'<rect x="{num(bx)}" y="{num(by + board_h + 30)}" width="{num(board_w)}" height="3" '
            f'rx="1.5" fill="#2c303d"/>'
            f'<rect class="a" id="progress" x="{num(bx)}" y="{num(by + board_h + 30)}" '
            f'width="{num(board_w)}" height="3" rx="1.5" fill="{theme.ACCENT}"/>'
        )
        said = "".join(captions)
    else:
        # A still is the last frame -- what reduced motion shows: the board finished and lit.
        motion, under, said = "", "", ""

    css = f"""
#back{{opacity:0}}
path.a{{fill:none;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1 1}}
text{{font-family:{FONT_STACK}}}{motion}
"""
    outline = (
        f'<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="{num(corner - 0.5)}" '
        f'fill="none" stroke="#2c303d"/>'
        if corner
        else ""
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-labelledby="t">
<title id="t">{escape(words.title)}</title>
<style>{css}</style>
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e2029"/><stop offset="1" stop-color="#14161c"/></linearGradient>
<radialGradient id="halo" cx="{num(cx)}" cy="{num(cy)}" r="{num(board_w * 0.83)}" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{theme.ACCENT}" stop-opacity=".22"/><stop offset="1" stop-color="{theme.ACCENT}" stop-opacity="0"/></radialGradient>
<radialGradient id="field" cx="{num(cx)}" cy="{num(cy)}" r="{num(board_w * 1.1)}" gradientUnits="userSpaceOnUse"><stop offset=".35" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
<mask id="fade"><rect width="{width}" height="{height}" fill="url(#field)"/></mask>
<pattern id="dots" x="{num(bx)}" y="{num(by)}" width="{num(p)}" height="{num(p)}" patternUnits="userSpaceOnUse"><circle cx="{num(p / 2)}" cy="{num(p / 2)}" r="2.4" fill="#fff" opacity=".07"/></pattern>
<pattern id="pads" x="{num(bx)}" y="{num(by)}" width="{num(p)}" height="{num(p)}" patternUnits="userSpaceOnUse"><circle cx="{num(p / 2)}" cy="{num(p / 2)}" r="{num(pad_r)}" fill="{scheme.pad}" stroke="{scheme.pad_ring}" stroke-width="1.2"/><path d="M{num(p / 2 - pad_r * 0.7)} {num(p / 2 - pad_r * 0.25)}a{num(pad_r * 0.75)} {num(pad_r * 0.75)} 0 0 1 {num(pad_r * 0.95)} {num(-pad_r * 0.5)}" stroke="{scheme.pad_sheen}" stroke-width="1.6" fill="none" stroke-linecap="round"/><circle cx="{num(p / 2)}" cy="{num(p / 2)}" r="{num(hole_r)}" fill="#0e1210"/></pattern>
<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".07"/><stop offset=".5" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".16"/></linearGradient>
<linearGradient id="shade-x" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".38"/><stop offset=".45" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".32"/></linearGradient>
<linearGradient id="shade-y" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity=".38"/><stop offset=".45" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".32"/></linearGradient>
<linearGradient id="case" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".09"/><stop offset="1" stop-color="#000" stop-opacity=".2"/></linearGradient>
<radialGradient id="dome" cx=".38" cy=".34" r=".7"><stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
<radialGradient id="fillet" cx=".4" cy=".35" r=".7"><stop offset="0" stop-color="#eef0f3"/><stop offset=".6" stop-color="#b9bec6"/><stop offset="1" stop-color="#7d838c"/></radialGradient>
<radialGradient id="lit-lens" cx=".45" cy=".4" r=".6"><stop offset="0" stop-color="#fff4e8"/><stop offset=".45" stop-color="#ff8a6e"/><stop offset="1" stop-color="#ff4a3a" stop-opacity=".6"/></radialGradient>
<radialGradient id="led-glow"><stop offset="0" stop-color="#ff5a46" stop-opacity=".95"/><stop offset=".3" stop-color="#ff5a46" stop-opacity=".38"/><stop offset="1" stop-color="#ff5a46" stop-opacity="0"/></radialGradient>
<filter id="lift" x="-30%" y="-30%" width="160%" height="170%"><feDropShadow dx="0" dy="2.5" stdDeviation="2.2" flood-color="#000" flood-opacity=".5"/></filter>
<filter id="soft" x="-20%" y="-40%" width="140%" height="180%"><feGaussianBlur stdDeviation="14"/></filter>
</defs>
<rect width="{width}" height="{height}" rx="{num(corner)}" fill="url(#bg)"/>
<rect width="{width}" height="{height}" rx="{num(corner)}" fill="url(#dots)" mask="url(#fade)"/>
<rect width="{width}" height="{height}" rx="{num(corner)}" fill="url(#halo)"/>
{outline}
{"".join(words_svg)}
<g class="a" id="board">
{face_svg("back", "".join(fillets) + "".join(copper_svg))}
{face_svg("front", "".join(parts_front))}
<circle class="a" id="glow" cx="{num(led.x)}" cy="{num(led.y)}" r="{num(led.w * 2.6)}" fill="url(#led-glow)" style="mix-blend-mode:screen"/>
{under}
</g>
{said}
</svg>
"""


def main() -> int:
    document = build_board()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for language, name in (("en", "banner.svg"), ("tr", "banner.tr.svg")):
        svg = render(document, WORDS[language], BANNER)
        (OUT_DIR / name).write_text(svg, encoding="utf-8", newline="\n")
        print(f"{name:16} {len(svg.encode('utf-8')) // 1024:4} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
