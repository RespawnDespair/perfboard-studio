"""What a click in the 3D view lands on, and what being selected looks like there.

The 3D view was only ever looked at: the board and the sheet cross-probe, and with the
three views side by side a part found in 3D had to be found again in 2D by eye. A click
now names the part or conductor under it (``view3d.owner_at``), and what is selected
anywhere is tinted in the actors already built (``view3d.show_selection``).
"""

from __future__ import annotations

import dataclasses

import pytest

from perfboard_studio.model import ComponentInstance, HoleCoord, WireConductor

from .test_gl import requires_offscreen_gl

vtk = pytest.importorskip("vtk")


def _one_part_and_one_jumper():
    """A DIP and a top jumper on an otherwise empty board, apart, nothing in front of
    either from the default camera."""
    from perfboard_studio.mcp.session import new_board

    document = new_board(cols=30, rows=20)
    part = ComponentInstance(
        id="part-u1", ref="U1", value="NE555", footprint_id="dip-8", anchor=HoleCoord(col=6, row=4)
    )
    jumper = WireConductor(
        id="wire-1",
        path=(HoleCoord(col=4, row=15), HoleCoord(col=20, row=15)),
        kind="top-jumper",
        side="top",
    )
    return dataclasses.replace(document, components=(part,), conductors=(jumper,))


@requires_offscreen_gl
def test_a_click_names_the_part_or_the_wire_it_lands_on() -> None:
    """At the top of each thing as drawn, the click names that thing; on the bare room, it
    names nothing. Measured through the renderer the window uses, render passes and all --
    the hardware selector ``vtkPropPicker`` uses picks nothing through them."""
    from perfboard_studio.footprints import footprint_lookup
    from perfboard_studio.ui import view3d

    document = _one_part_and_one_jumper()
    owners: dict = {}
    renderer, _stats = view3d.build_renderer(document, footprint_lookup(), owners=owners)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.AddRenderer(renderer)
    window.SetSize(900, 700)
    view3d.apply_default_camera(renderer)
    window.Render()

    for owner in (("component", "part-u1"), ("conductor", "wire-1")):
        bounds = [actor.GetBounds() for actor, whose in owners.items() if whose == owner]
        assert bounds, owner
        x = (min(b[0] for b in bounds) + max(b[1] for b in bounds)) / 2
        y = (min(b[2] for b in bounds) + max(b[3] for b in bounds)) / 2
        z = max(b[5] for b in bounds)
        renderer.SetWorldPoint(x, y, z, 1.0)
        renderer.WorldToDisplay()
        px, py, _pz = renderer.GetDisplayPoint()
        assert view3d.owner_at(renderer, owners, round(px), round(py)) == owner

    assert view3d.owner_at(renderer, owners, 3, 3) is None


def test_the_selection_tint_is_given_back() -> None:
    """A tint applied to what is selected comes off exactly when it stops being selected,
    and the call says whether anything changed, so a frame is drawn only when one must be."""
    from perfboard_studio.ui import view3d

    chosen, other = vtk.vtkActor(), vtk.vtkActor()
    for actor in (chosen, other):
        actor.GetProperty().SetColor(0.2, 0.3, 0.4)
        actor.GetProperty().SetRoughness(0.5)
    owners = {chosen: ("component", "a"), other: ("component", "b")}
    tinted: dict = {}

    assert view3d.show_selection(owners, {"a"}, tinted)
    assert chosen.GetProperty().GetColor() != (0.2, 0.3, 0.4)
    assert other.GetProperty().GetColor() == pytest.approx((0.2, 0.3, 0.4))
    assert not view3d.show_selection(owners, {"a"}, tinted), "nothing to change"

    assert view3d.show_selection(owners, set(), tinted)
    assert chosen.GetProperty().GetColor() == pytest.approx((0.2, 0.3, 0.4))
    assert chosen.GetProperty().GetRoughness() == pytest.approx(0.5)
    assert tinted == {}
