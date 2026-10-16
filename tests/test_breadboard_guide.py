"""The build guide for a solderless breadboard: no iron, jumpers first, ICs across the
groove, and every check made once the parts it probes are in."""

from __future__ import annotations

import dataclasses
import html
import re

from perfboard_studio.breadroute import plan_breadboard
from perfboard_studio.footprints import get_footprint
from perfboard_studio.geometry import path_length_mm
from perfboard_studio.guide import (
    BREADBOARD_STRIP_MM,
    ConductorStep,
    GuideOptions,
    PartStep,
    build_guide,
)
from perfboard_studio.guide_export import guide_to_html
from tests.test_breadroute import TIMER


def _wired():  # type: ignore[no-untyped-def]
    return plan_breadboard(TIMER, get_footprint).document


def _text(guide) -> str:  # type: ignore[no-untyped-def]
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", guide_to_html(guide))))


def test_a_breadboard_guide_has_no_iron_and_never_says_solder() -> None:
    guide = build_guide(_wired(), get_footprint)
    assert guide.iron is None
    # "solderless" and "nothing on it is ... soldered" are the board saying what it is; an
    # instruction to solder, an iron or flux is the guide of a different board.
    assert not re.findall(r"\b(solder|soldering|iron|flux)\b", _text(guide).lower())
    assert any("Solid-core" in tool for tool in guide.tools)


def test_the_jumpers_go_in_first_and_the_ics_across_the_groove_next() -> None:
    guide = build_guide(_wired(), get_footprint)
    phases = {phase.number: phase for phase in guide.phases}
    conductors = [s for p in guide.phases for s in p.steps if isinstance(s, ConductorStep)]
    assert conductors and all(s in phases[1].steps for s in conductors)
    first_part = next(i for i, s in enumerate(phases[1].steps) if isinstance(s, PartStep))
    assert all(isinstance(s, ConductorStep) for s in phases[1].steps[:first_part])
    (chip,) = [s for s in phases[2].steps if isinstance(s, PartStep)]
    assert chip.ref == "U1"
    assert any(note.startswith("Across the groove, pin 1 in e11") for note in chip.notes)


def test_every_check_waits_for_the_parts_it_probes() -> None:
    """The jumpers are in before any part, so a net's copper is finished in phase 1 -- and
    a probe on a pin that is not in the board yet is a check that fails for no fault."""
    guide = build_guide(_wired(), get_footprint)
    phase_of = {
        s.ref: phase.number for phase in guide.phases for s in phase.steps if isinstance(s, PartStep)
    }
    for phase in guide.phases:
        for check in phase.checkpoints:
            if check.kind != "continuity":
                continue
            refs = re.findall(r"\b([A-Z]+\d+)\.", check.title)
            assert refs and all(phase_of[ref] <= phase.number for ref in refs), check.title


def test_a_jumper_is_cut_to_its_path_and_two_stripped_legs() -> None:
    """Nothing goes THROUGH a breadboard: the stripped ends are the whole of the legs, not
    two board thicknesses and two bends on top."""
    doc = _wired()
    guide = build_guide(doc, get_footprint)
    for cut in guide.cut_list:
        assert cut.strip_mm == BREADBOARD_STRIP_MM
        path = next(c.path for c in doc.conductors if c.id == cut.conductor_id)
        assert abs(cut.cut_mm - (path_length_mm(path, doc.board) + 2 * BREADBOARD_STRIP_MM)) < 1e-9


def test_a_breadboard_guide_is_turkish_too() -> None:
    from tests.test_phrasebook import _leaks

    assert _leaks(_wired(), get_footprint) == []
    turkish = build_guide(_wired(), get_footprint, GuideOptions(language="tr"))
    assert "Kurulum rehberi · lehimsiz breadboard, 830 nokta" in _text(turkish)


def test_a_chip_on_an_adapter_still_needs_the_iron_for_its_adapter() -> None:
    """The one soldering a breadboard build has: the chip onto its adapter, in phase 0."""
    doc = _wired()
    adapted = dataclasses.replace(
        doc,
        components=tuple(
            dataclasses.replace(c, footprint_id="adp-soic-8") if c.ref == "U1" else c
            for c in doc.components
        ),
    )
    guide = build_guide(adapted, get_footprint)
    assert guide.iron is not None
    assert "Solder the surface-mount chips onto their adapters first" in guide.phases[0].summary
