"""A surface-mount chip on a DIP adapter: the board a perfboard takes a SOIC on.

Nearly every new chip is surface-mount, and the way a hobbyist puts one on perfboard is a
small adapter board numbered as the DIP of the same pin count. These tests pin what had to be
learnt for it: the id grammar, the numbering (the DIP's, pin for pin), the outline the makers
actually sell, the height the header's tails can set, the KiCad footprints that now arrive on
one instead of being refused, the catalog part that keeps its names in the smaller package,
the guide that has the chip soldered on in phase 0 and the adapter fitted as a DIP, and both
views drawing the chip inside the envelope DRC measures.
"""

from __future__ import annotations

import pytest

from perfboard_studio.catalog import CATALOG
from perfboard_studio.command import CommandBus, CommandContext, create_id_generator
from perfboard_studio.commands import (
    PlaceComponentPayload,
    create_empty_document,
    create_standard_registry,
)
from perfboard_studio.footprints import (
    ADAPTER_PCB_MM,
    ADAPTER_PIN_TAIL_MM,
    ADAPTER_SEAT_MM,
    COURTYARD_MARGIN_MM,
    GENERATED_ID_GRAMMAR,
    adapter_package,
    body_extent,
    dip_footprint,
    footprint_lookup,
    get_footprint,
)
from perfboard_studio.geometry import format_hole
from perfboard_studio.guide import GuideOptions, PartStep, build_guide
from perfboard_studio.model import ComponentInstance, DocumentMeta, HoleCoord, PerfDocument
from perfboard_studio.parsers.kicad import ImportedComponent
from perfboard_studio.parsers.kicad_parts import _suggest, kicad_footprint_ids
from perfboard_studio.schematic import build_schematic

REGISTRY = footprint_lookup()
META = DocumentMeta(name="adapter", created="", modified="")
PITCH = 2.54
TURNS = (0, 90, 180, 270)

ON_ADAPTER = "its footprint is surface-mount, so it goes on a DIP adapter, numbered pin for pin"
REFUSED = "its footprint is surface-mount, which a perfboard cannot take"


def _footprint(footprint_id: str):
    footprint = get_footprint(footprint_id)
    assert footprint is not None, footprint_id
    return footprint


# ---------------------------------------------------------------------------
# The id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "id_,package,rows",
    [
        ("adp-soic-8", "SOIC-8", 3),
        ("adp-soic-14", "SOIC-14", 6),
        ("adp-soic-16", "SOIC-16", 6),
        ("adp-soic-16-r3", "SOIC-16", 3),
        ("adp-soic-8-r6", "SOIC-8", 6),
        ("adp-tssop-20", "TSSOP-20", 6),
        ("adp-ssop-28", "SSOP-28", 6),
        ("adp-msop-8", "MSOP-8", 3),
        ("adp-msop-10", "MSOP-10", 6),
        # Too wide to sit between rows 0.3 inch apart, so its usual adapter is 0.6.
        ("adp-ssop-8", "SSOP-8", 6),
    ],
)
def test_an_id_is_its_package_its_pins_and_its_rows(id_: str, package: str, rows: int) -> None:
    footprint = _footprint(id_)
    assert footprint.id == id_
    assert footprint.body.archetype == "smd-adapter"
    assert adapter_package(footprint) == package
    assert {pin.d_col for pin in footprint.pins} == {0, rows}
    assert footprint.body.dims["rowSpacing"] == pytest.approx(rows * PITCH)


@pytest.mark.parametrize(
    "id_",
    [
        "adp-soic-8-r3",  # the usual rows are spelled by leaving them out
        "adp-soic-16-r6",
        "adp-ssop-8-r6",
        "adp-soic-08",
        "adp-soic-7",  # odd
        "adp-soic-6",  # no adapter is sold for fewer than eight
        "adp-soic-58",
        "adp-soic-8-r2",
        "adp-soic-28-r3",  # a wide-body SOIC does not fit between rows 0.3 inch apart
        "adp-qfn-8",
        "adp-SOIC-8",
    ],
)
def test_one_spelling_per_part_and_nothing_impossible(id_: str) -> None:
    assert get_footprint(id_) is None


def test_the_grammar_says_how_to_ask_for_one() -> None:
    assert "adp-<package>-<pins>[-r<rowStep>]" in GENERATED_ID_GRAMMAR


def test_only_an_adapter_names_a_package() -> None:
    assert adapter_package(_footprint("dip-8")) is None
    assert adapter_package(_footprint("mod-2x4-p3-r1-10x10x2-s2.5")) is None


# ---------------------------------------------------------------------------
# The numbering and the outline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", ["soic", "tssop"])
@pytest.mark.parametrize("pins", [8, 14, 16, 20, 28])
def test_it_is_numbered_as_the_dip_it_stands_in_for(package: str, pins: int) -> None:
    """Every adapter brings pin N of the chip out on pin N of the DIP -- "pin connection
    1:1", as Gotronik's sheet says -- which is the whole reason a netlist drawn for the
    SOIC lands on the board without a pin moving."""
    footprint = _footprint(f"adp-{package}-{pins}")
    wide = footprint.body.dims["rowSpacing"] > 3 * PITCH
    dip = dip_footprint(pin_count=pins, wide=wide)
    assert [(p.number, p.d_col, p.d_row) for p in footprint.pins] == [
        (p.number, p.d_col, p.d_row) for p in dip.pins
    ]


def test_rows_are_a_third_of_an_inch_for_eight_pins_and_six_tenths_above() -> None:
    """SparkFun's BOB-13655, ProtoSupplies' and Adafruit's (1212) eight-pin boards are 0.3
    inch; Adafruit's 14-, 16- and 20-pin (1210, 1207, 1206) and Gotronik's 28-pin PCB-124
    are 0.6."""
    assert _footprint("adp-soic-8").body.dims["rowSpacing"] == pytest.approx(7.62)
    for pins in (14, 16, 20, 28):
        assert _footprint(f"adp-soic-{pins}").body.dims["rowSpacing"] == pytest.approx(15.24)


@pytest.mark.parametrize(
    "id_,width,length,tolerance",
    [
        ("adp-soic-8", 10.16, 10.16, 1e-9),  # SparkFun BOB-13655
        ("adp-soic-28", 17.78, 35.56, 1e-9),  # Gotronik PCB-124
        ("adp-soic-20", 17.79, 25.63, 0.25),  # Adafruit 1206
    ],
)
def test_the_board_is_the_outline_the_makers_sell(
    id_: str, width: float, length: float, tolerance: float
) -> None:
    dims = _footprint(id_).body.dims
    assert dims["width"] == pytest.approx(width, abs=tolerance)
    assert dims["length"] == pytest.approx(length, abs=tolerance)


@pytest.mark.parametrize("id_", ["adp-soic-8", "adp-tssop-20", "adp-soic-16-r3"])
def test_the_body_is_the_board_and_the_courtyard_half_a_pitch_round_it(id_: str) -> None:
    footprint = _footprint(id_)
    dims = footprint.body.dims
    extent = body_extent(footprint, PITCH)
    assert (extent.size_x, extent.size_y) == pytest.approx((dims["width"], dims["length"]))
    xs = [p.x for p in footprint.body_outline]
    ys = [p.y for p in footprint.body_outline]
    min_x, max_x, min_y, max_y = extent.box
    assert (min(xs), max(xs)) == pytest.approx(
        (min_x - COURTYARD_MARGIN_MM, max_x + COURTYARD_MARGIN_MM)
    )
    assert (min(ys), max(ys)) == pytest.approx(
        (min_y - COURTYARD_MARGIN_MM, max_y + COURTYARD_MARGIN_MM)
    )


def test_the_chip_fits_on_its_board_between_its_rows() -> None:
    for id_ in ("adp-soic-8", "adp-soic-28", "adp-tssop-28", "adp-ssop-28", "adp-msop-10"):
        dims = _footprint(id_).body.dims
        assert dims["chipSpan"] < dims["rowSpacing"]
        assert dims["chipLength"] < dims["length"]


def test_its_height_is_the_chip_or_the_header_tails_whichever_stands_higher() -> None:
    """A SOIC stands above the 1.4 mm of tail a header leaves through the board; a TSSOP
    does not, and an envelope that ended at the chip would let a lid be drawn into the pins."""
    soic = _footprint("adp-soic-8")
    assert soic.body_height == pytest.approx(
        ADAPTER_SEAT_MM + ADAPTER_PCB_MM + soic.body.dims["chipHeight"]
    )
    # Over 16 pins a SOIC is wide-body only, and taller.
    assert _footprint("adp-soic-28").body.dims["chipHeight"] == pytest.approx(2.65)
    tssop = _footprint("adp-tssop-20")
    assert tssop.body_height == pytest.approx(ADAPTER_SEAT_MM + ADAPTER_PIN_TAIL_MM)


def test_on_the_sheet_it_is_an_ic() -> None:
    bus = _bus()
    _place(bus, "U1", "adp-soic-8", "LM358")
    drawing = build_schematic(bus.document, REGISTRY)
    assert next(s for s in drawing.symbols if s.ref == "U1").kind == "ic"


# ---------------------------------------------------------------------------
# KiCad
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,ids",
    [
        ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", ["adp-soic-8"]),
        ("Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm", ["adp-soic-16"]),
        ("Package_SO:SOP-8_3.9x4.9mm_P1.27mm", ["adp-soic-8"]),
        ("Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm", ["adp-tssop-20"]),
        ("Package_SO:SSOP-28_5.3x10.2mm_P0.65mm", ["adp-ssop-28"]),
        ("Package_SO:MSOP-10_3x3mm_P0.5mm", ["adp-msop-10"]),
        # A 3 mm VSSOP is an MSOP by every measure but its name.
        ("Package_SO:VSSOP-8_3.0x3.0mm_P0.65mm", ["adp-msop-8"]),
    ],
)
def test_kicads_gull_wing_packages_go_on_an_adapter(name: str, ids: list[str]) -> None:
    assert kicad_footprint_ids(name) == (ids, ON_ADAPTER)
    assert all(get_footprint(i) is not None for i in ids)


@pytest.mark.parametrize(
    "name",
    [
        # An exposed pad no adapter brings out, and packages no DIP adapter takes.
        "Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.29x3mm",
        "Package_TO_SOT_SMD:SOT-23",
        "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
    ],
)
def test_what_no_adapter_takes_is_still_refused(name: str) -> None:
    assert kicad_footprint_ids(name) == ([], REFUSED)


def test_an_adapter_nobody_makes_is_refused_as_surface_mount() -> None:
    """KiCad's SOP-4 is an optocoupler; the name parses, the adapter does not exist."""
    component = ImportedComponent(
        ref="U2", value="", footprint="Package_SO:SOP-4_3.8x4.1mm_P2.54mm", lib_part=None
    )
    suggestion, notes = _suggest(component, 4, REGISTRY)
    assert not suggestion.footprint_id.startswith("adp-")
    assert notes == [REFUSED]


def test_an_lm358_in_a_soic_is_the_catalogs_lm358_on_an_adapter() -> None:
    """The same die bonded out in the same order: the catalog's names are the DIP's and
    the adapter's alike, so the part keeps them in the smaller package."""
    component = ImportedComponent(
        ref="U1",
        value="LM358",
        footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        lib_part="Amplifier_Operational:LM358",
    )
    suggestion, notes = _suggest(component, 8, REGISTRY)
    lm358 = next(part for part in CATALOG if part.id == "lm358")
    assert suggestion.footprint_id == "adp-soic-8"
    assert suggestion.source == "catalog"
    assert suggestion.pin_names == lm358.pin_names
    assert ON_ADAPTER in notes


def test_a_catalog_part_in_a_package_with_other_pins_is_not_that_part() -> None:
    """An L293D is a DIP-16; the SO-20 sold under nearly the name (L293DD) is pinned
    differently. A pin count that disagrees is a different chip, and its names are not
    borrowed."""
    component = ImportedComponent(
        ref="U3",
        value="L293D",
        footprint="Package_SO:SOIC-20W_7.5x12.8mm_P1.27mm",
        lib_part=None,
    )
    suggestion, notes = _suggest(component, 20, REGISTRY)
    assert suggestion.footprint_id == "adp-soic-20"
    assert suggestion.source == "kicad"
    assert suggestion.pin_names == ()
    assert ON_ADAPTER in notes


# ---------------------------------------------------------------------------
# The guide
# ---------------------------------------------------------------------------


def _bus() -> CommandBus:
    return CommandBus(
        create_empty_document(META),
        create_standard_registry(),
        CommandContext(next_id=create_id_generator()),
    )


def _place(bus: CommandBus, ref: str, footprint_id: str, value: str, col: int = 4) -> None:
    result = bus.dispatch(
        "component.place",
        PlaceComponentPayload(
            ref=ref, value=value, footprint_id=footprint_id, anchor=HoleCoord(col=col, row=4)
        ),
    )
    assert result.ok, result.message


def _adapter_board() -> PerfDocument:
    bus = _bus()
    _place(bus, "U1", "adp-soic-8", "LM358")
    _place(bus, "U2", "adp-tssop-20", "CH340C", col=14)
    _place(bus, "R1", "r-axial-3", "10k", col=30)
    return bus.document


def _step(guide, ref: str) -> tuple[int, PartStep]:
    for phase in guide.phases:
        for step in phase.steps:
            if isinstance(step, PartStep) and step.ref == ref:
                return phase.number, step
    raise AssertionError(ref)


def test_it_goes_in_as_a_dip_and_says_which_way_round() -> None:
    doc = _adapter_board()
    guide = build_guide(doc, REGISTRY)
    phase, step = _step(guide, "U1")
    assert phase == 2
    u1 = next(c for c in doc.components if c.ref == "U1")
    text = " ".join(step.notes)
    assert "A SOIC-8 on its DIP adapter" in text
    assert "goes in with the other ICs in phase 8" in text
    assert step.polarity == (
        f'Pin 1 (the adapter\'s "1", beside the chip\'s dot) in {format_hole(u1.anchor)}'
    )


def test_the_chips_go_onto_their_adapters_in_phase_zero() -> None:
    guide = build_guide(_adapter_board(), REGISTRY)
    summary = guide.phases[0].summary
    assert "Solder the surface-mount chips onto their adapters first" in summary
    assert "U1 (SOIC-8) and U2 (TSSOP-20)" in summary


def test_the_bench_list_counts_the_header_pins() -> None:
    guide = build_guide(_adapter_board(), REGISTRY)
    assert any("28 male header pins" in line for line in guide.tools)


def test_the_parts_list_says_what_to_buy() -> None:
    guide = build_guide(_adapter_board(), REGISTRY)
    names = {line.footprint_name for line in guide.bom}
    assert "SOIC-8 on a DIP adapter" in names
    assert 'TSSOP-20 on a DIP adapter (0.6" wide)' in names


def test_a_board_without_one_says_nothing_about_adapters() -> None:
    bus = _bus()
    _place(bus, "U1", "dip-8", "LM358")
    guide = build_guide(bus.document, REGISTRY)
    assert "adapter" not in guide.phases[0].summary
    assert not any("adapter" in line for line in guide.tools)


def test_the_turkish_guide_says_it_in_turkish() -> None:
    guide = build_guide(_adapter_board(), REGISTRY, GuideOptions(language="tr"))
    _phase, step = _step(guide, "U1")
    assert "DIP adaptörü üzerinde bir SOIC-8" in " ".join(step.notes)
    assert "adaptörün üzerindeki" in (step.polarity or "")
    assert "Önce yüzey montaj çipleri adaptörlerine lehimle" in guide.phases[0].summary
    assert any("28 erkek pin" in line for line in guide.tools)
    assert "SOIC-8, DIP adaptöründe" in {line.footprint_name for line in guide.bom}


# ---------------------------------------------------------------------------
# Both views
# ---------------------------------------------------------------------------


def _part(footprint_id: str, rotation: int = 0, mirrored: bool = False) -> ComponentInstance:
    return ComponentInstance(
        id="part-1",
        ref="U1",
        value="LM358",
        footprint_id=footprint_id,
        anchor=HoleCoord(col=20, row=20),
        rotation=rotation,  # type: ignore[arg-type]
        mirrored=mirrored,
    )


@pytest.mark.parametrize("rotation", TURNS)
@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("footprint_id", ["adp-soic-8", "adp-tssop-20", "adp-soic-28"])
def test_the_3d_adapter_stays_inside_its_envelope(
    footprint_id: str, rotation: int, mirrored: bool
) -> None:
    """Measured on the actors as built, as the vertical terminal is: the board, the header
    strips, the tails, the chip, its legs and its dot. What is drawn is what DRC measures."""
    from perfboard_studio.mcp.session import new_board
    from perfboard_studio.ui import view3d

    board = new_board(cols=40, rows=40).board
    component = _part(footprint_id, rotation, mirrored)
    footprint = _footprint(footprint_id)
    body = view3d._world_body(REGISTRY, component, board)
    assert body is not None
    pieces = view3d._pieces_for(body, footprint, component, board)
    above = [piece for piece in pieces if view3d._actor_for(piece).GetBounds()[5] > 0]
    assert len(above) >= 6
    for piece in above:
        x0, x1, y0, y1, _z0, z1 = view3d._actor_for(piece).GetBounds()
        assert z1 <= body.height + 1e-6, piece
        assert body.x - body.size_x / 2 - 1e-6 <= x0 and x1 <= body.x + body.size_x / 2 + 1e-6
        assert body.y - body.size_y / 2 - 1e-6 <= y0 and y1 <= body.y + body.size_y / 2 + 1e-6


def _qt_app():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["perfboard-studio-tests"])


def test_the_2d_view_draws_the_chip_on_the_adapter() -> None:
    """The board in its blue, the chip dark in the middle of it and the pins drawn over the
    board round it -- the picture that says this is not a DIP."""
    _qt_app()
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QGraphicsScene

    from perfboard_studio.mcp.session import new_board
    from perfboard_studio.ui.bodies import BODY_STYLES
    from perfboard_studio.ui.view2d import ComponentItem, _local_offset_mm

    board = new_board(cols=40, rows=40).board
    component = _part("adp-soic-8")
    item = ComponentItem(component, _footprint("adp-soic-8"), board, "top")
    scene = QGraphicsScene()
    scene.addItem(item)
    source = item.sceneBoundingRect()
    scale = 12.0
    image = QImage(
        int(source.width() * scale), int(source.height() * scale), QImage.Format.Format_ARGB32
    )
    image.fill(0)
    painter = QPainter(image)
    scene.render(painter, QRectF(0, 0, image.width(), image.height()), source)
    painter.end()

    def pixel(x_mm: float, y_mm: float) -> QColor:
        point = item.mapToScene(QPointF(*_local_offset_mm(x_mm, y_mm, component, "top")))
        return image.pixelColor(
            int((point.x() - source.left()) * scale), int((point.y() - source.top()) * scale)
        )

    centre = 1.5 * PITCH
    chip = pixel(centre, centre)
    assert chip.value() < 60
    # Between the chip's legs and a row of pins, along the end: bare adapter board.
    board_colour = QColor(BODY_STYLES["smd-adapter"].fill)
    bare = pixel(centre, -0.9)
    assert abs(bare.blue() - board_colour.blue()) < 40 and bare.blue() > bare.red()
    pin = pixel(0.0, PITCH)
    assert abs(pin.red() - board_colour.red()) > 60


def test_the_custom_part_dialog_builds_one() -> None:
    _qt_app()
    from PySide6.QtWidgets import QComboBox, QSpinBox

    from perfboard_studio.ui.main import CustomPartDialog

    dialog = CustomPartDialog()
    labels = [dialog.family.itemText(i) for i in range(dialog.family.count())]
    dialog.family.setCurrentIndex(labels.index("Surface-mount chip on a DIP adapter"))
    package = dialog._widgets["package"]
    pins = dialog._widgets["pins"]
    assert isinstance(package, QComboBox) and isinstance(pins, QSpinBox)
    assert dialog.chosen() is not None and dialog.chosen().id == "adp-soic-8"
    package.setCurrentIndex(package.findText("TSSOP"))
    pins.setValue(20)
    chosen = dialog.chosen()
    assert chosen is not None and chosen.id == "adp-tssop-20"
    dialog.deleteLater()
