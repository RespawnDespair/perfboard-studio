"""A breadboard in the window: drawn as plastic with holes and print, and chosen by size."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from perfboard_studio.breadboard import SIZES, breadboard, missing_holes
from perfboard_studio.commands import DEFAULT_BOARD
from perfboard_studio.footprints import footprint_lookup
from perfboard_studio.model import DocumentMeta, PerfDocument

LOOKUP = footprint_lookup()


def _app() -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication(["perfboard-studio-tests"])


def _scene(size: str, side: str = "top"):  # type: ignore[no-untyped-def]
    from perfboard_studio.ui.view2d import BoardScene

    doc = PerfDocument(meta=DocumentMeta(name="t", created="", modified=""), board=breadboard(size))
    return BoardScene(doc, LOOKUP, side=side, show_rulers=True)  # type: ignore[arg-type]


def _paint(scene) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtGui import QImage, QPainter

    image = QImage(800, 300, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    scene.render(painter)
    painter.end()


def test_a_breadboard_is_drawn_as_plastic_with_every_hole_and_no_copper() -> None:
    """No pads, no strips, no legend: its own item, which draws exactly the holes the board
    is sold with -- the pattern, not the grid."""
    _app()
    from perfboard_studio.ui.view2d import BoardLegendItem, HoleRulerItem, StripGridItem

    for size in SIZES:
        scene = _scene(size)
        assert scene.pad_grid is None and scene.breadboard_item is not None
        assert not any(isinstance(i, (BoardLegendItem, HoleRulerItem, StripGridItem)) for i in scene.items())
        _paint(scene)
        board = breadboard(size)
        assert scene.breadboard_item.drawn == board.cols * board.rows - len(missing_holes(board)) == int(size)


def test_the_board_names_its_own_holes_so_no_ruler_is_drawn_over_them() -> None:
    """It prints e12 on its face; a grid ruler of A, B, C beside that would name the holes
    a second way. From underneath there is no print, and the ruler is wanted again."""
    _app()
    assert _scene("830").legend_is_readable()
    assert not _scene("830", side="bottom").legend_is_readable()


def test_board_setup_asks_a_breadboard_only_its_size() -> None:
    _app()
    from perfboard_studio.ui.main import BoardSetupDialog

    dialog = BoardSetupDialog(DEFAULT_BOARD)
    dialog.board_type.setCurrentIndex(dialog.board_type.findData("breadboard"))
    dialog.breadboard_size.setCurrentIndex(dialog.breadboard_size.findData("400"))
    assert dialog.board() == breadboard("400")
    assert dialog.preset_features() is None
    form = dialog._chosen
    assert form.isRowVisible(dialog.breadboard_size)
    assert not form.isRowVisible(dialog.preset)
    assert not dialog.advanced_toggle.isVisibleTo(dialog)

    dialog.board_type.setCurrentIndex(dialog.board_type.findData("pad-per-hole"))
    assert not form.isRowVisible(dialog.breadboard_size)
    assert form.isRowVisible(dialog.preset)


def test_board_setup_opens_on_the_breadboard_it_was_given() -> None:
    _app()
    from perfboard_studio.ui.main import BoardSetupDialog

    dialog = BoardSetupDialog(breadboard("170"))
    assert dialog.board_type.currentData() == "breadboard"
    assert dialog.breadboard_size.currentData() == "170"
    assert dialog.board() == breadboard("170")



@pytest.fixture
def window(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """A window on an empty breadboard, its settings kept out of the real store."""
    _app()
    from PySide6.QtCore import QSettings

    from perfboard_studio.ui import main as main_module

    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(main_module, "app_settings", lambda: store)
    doc = PerfDocument(meta=DocumentMeta(name="t", created="", modified=""), board=breadboard("830"))
    made = main_module.MainWindow(doc)
    yield made
    made._saved_document = made.bus.document
    made.close()


def test_a_breadboard_offers_only_the_copper_it_can_take(window) -> None:  # type: ignore[no-untyped-def]
    """Solder, and bare wire on the solder side, are greyed out on a breadboard -- and come
    back the moment the board is changed to one that can take them."""
    from perfboard_studio.commands import SetBoardPayload

    enabled = {kind: action.isEnabled() for kind, action in window.act_draw.items()}
    assert enabled == {
        "solder-trace": False,
        "solder-trace-wired": False,
        "bare-wire": False,
        "insulated-wire": True,
        "top-jumper": True,
    }
    assert window.bus.dispatch("board.set", SetBoardPayload(board=DEFAULT_BOARD)).ok
    assert all(action.isEnabled() for action in window.act_draw.values())


def test_no_stock_perfboard_is_offered_for_a_breadboard() -> None:
    """A perfboard's size applied to a breadboard is a board board.set refuses, so the
    window's Board step goes to Board Setup -- the breadboard's sizes -- instead."""
    from perfboard_studio.placer import suggest_boards

    assert suggest_boards(breadboard("830"), (), (), LOOKUP) == ()
    assert suggest_boards(DEFAULT_BOARD, (), (), LOOKUP) != ()


def test_a_jumper_drawn_on_a_breadboard_goes_in_from_above(window) -> None:  # type: ignore[no-untyped-def]
    from perfboard_studio.breadboard import parse_hole_name
    from perfboard_studio.drc import run_drc

    scene = window.scene
    scene.arm_drawing("top-jumper")
    a, b = (parse_hole_name(window.bus.document.board, name) for name in ("a5", "a9"))
    assert a is not None and b is not None
    scene.draw_click(a)
    result = scene.draw_click(b)  # a wire is two clicks, and the second commits it
    assert result is not None and result.ok, result
    conductor = window.bus.document.conductors[0]
    assert conductor.side == "top"
    assert not [v for v in run_drc(window.bus.document, LOOKUP) if v.rule == "not-on-breadboard"]
