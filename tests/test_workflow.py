"""Tests for the step bar (src/perfboard_studio/ui/workflow.py).

The steps are measured off the document, so the interesting half is a pure function of
counts -- ``workflow_steps`` -- and is tested here by handing it counts. The window half --
that the bar follows the document and each step goes where it is done -- is in
``test_ui.py``, beside the fixtures that keep a test window out of the user's settings.
"""

from __future__ import annotations

import dataclasses

from perfboard_studio.ui.workflow import (
    STEP_ORDER,
    WorkflowFacts,
    next_step,
    workflow_steps,
)

EMPTY = WorkflowFacts(
    parts_off_board=0,
    parts_on_board=0,
    nets=0,
    board_label="5 x 7 cm · 18 × 24",
    board_chosen=False,
    unrouted=0,
    drc_errors=0,
    drc_warnings=0,
    lvs_problems=0,
)


def statuses(facts: WorkflowFacts) -> dict[str, str]:
    return {step.key: step.status for step in workflow_steps(facts)}


def test_a_new_document_starts_at_the_circuit_and_nothing_is_done() -> None:
    steps = statuses(EMPTY)
    assert steps["circuit"] == "current"
    assert all(status == "todo" for key, status in steps.items() if key != "circuit")
    assert next_step(workflow_steps(EMPTY)) == "circuit"


def test_the_steps_are_in_the_order_a_board_is_built() -> None:
    assert STEP_ORDER == ("circuit", "board", "place", "route", "check", "build")
    assert [step.key for step in workflow_steps(EMPTY)] == list(STEP_ORDER)


def test_parts_without_a_single_join_are_not_a_circuit_yet() -> None:
    """Eight parts and no nets is a parts list: routing it would do nothing."""
    facts = dataclasses.replace(EMPTY, parts_off_board=8)
    assert statuses(facts)["circuit"] == "current"


def test_a_drawn_circuit_moves_on_to_choosing_its_board() -> None:
    facts = dataclasses.replace(EMPTY, parts_off_board=8, nets=7)
    steps = statuses(facts)
    assert steps["circuit"] == "done"
    assert steps["board"] == "current"
    assert next_step(workflow_steps(facts)) == "board"


def test_a_chosen_board_moves_on_to_placing() -> None:
    facts = dataclasses.replace(EMPTY, parts_off_board=8, nets=7, board_chosen=True)
    assert statuses(facts)["board"] == "done"
    assert next_step(workflow_steps(facts)) == "place"


def test_a_board_with_parts_on_it_has_been_chosen() -> None:
    """Nobody has to press Choose a Board on a document opened with a layout in it."""
    facts = dataclasses.replace(EMPTY, parts_on_board=3, parts_off_board=5, nets=4)
    steps = statuses(facts)
    assert steps["board"] == "done"
    assert steps["place"] == "current"


def test_a_placed_board_with_connections_left_is_waiting_for_its_wiring() -> None:
    facts = dataclasses.replace(EMPTY, parts_on_board=8, nets=7, unrouted=14)
    steps = statuses(facts)
    assert steps["place"] == "done"
    assert steps["route"] == "current"
    route = next(step for step in workflow_steps(facts) if step.key == "route")
    assert "14" in route.summary


def test_a_clean_routed_board_is_ready_to_build() -> None:
    facts = dataclasses.replace(EMPTY, parts_on_board=8, nets=7, drc_warnings=2)
    steps = statuses(facts)
    assert steps["route"] == "done"
    assert steps["check"] == "done", "warnings are for reading, not for stopping"
    assert steps["build"] == "current"
    assert next_step(workflow_steps(facts)) == "build"


def test_an_error_is_a_problem_and_the_next_button_goes_to_it_first() -> None:
    """A DRC error or an open net is where the button goes, whatever else is left: the
    board as drawn cannot be built, and the rest of the steps are built on it."""
    facts = dataclasses.replace(EMPTY, parts_on_board=8, nets=7, unrouted=3, drc_errors=1)
    steps = statuses(facts)
    assert steps["check"] == "problem"
    assert steps["route"] == "current"
    assert next_step(workflow_steps(facts)) == "check"

    open_net = dataclasses.replace(EMPTY, parts_on_board=8, nets=7, lvs_problems=1)
    assert statuses(open_net)["check"] == "problem"


def test_nothing_on_the_board_is_nothing_to_check() -> None:
    """LVS reports every net as unplaced before anything is placed. That is the placement
    step's news, and reporting it again as a check failure would put the warning colour on
    a board nobody has started."""
    facts = dataclasses.replace(EMPTY, parts_off_board=8, nets=7, lvs_problems=7)
    assert statuses(facts)["check"] == "todo"
