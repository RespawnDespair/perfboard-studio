"""Breadboard: the board whose connections are a pattern nobody can change.

A solderless breadboard is a block of plastic over rows of spring clips. Every lead pushed
into a hole is gripped by the clip under it, and every hole over one clip is joined to the
others: five holes across each half of a column, and a long run down each power rail.
Nothing is soldered and nothing can be cut, so the whole design problem is WHERE THINGS GO
-- two pins of different nets in one clip are shorted by the board, and no wire changes
that. It is stripboard's problem with the scissors taken away (see ``stripboard.py``,
whose segments this module supplies on a breadboard).

THE PATTERN IS THE BOARD'S SHAPE, not a document field. A breadboard is ``board.type ==
"breadboard"`` and its grid says the rest: 20 rows is a board with power rails, 12 rows is
one without, and the columns are the numbers printed along it. Nothing new goes in the
file, so the format does not move -- the same reason ``BodySpec`` keeps its numbers in an
id rather than a field.

WHAT THE PATTERN IS, MEASURED rather than remembered: Fritzing's drawing of the RSR
03MB102 (``core/breadboard2.fzp`` in fritzing-parts), the 830-point board sold in every
starter kit, read for hole positions and rail colours only (see ``docs/prior-art.md``).
Every hole sits on the 0.1 inch grid. From the top, with column 1 on the left:

    row  0      - rail   (blue line above it)
    row  1      + rail   (red line below it)
    rows 2-3    no holes
    rows 4-8    j i h g f      one clip per column
    rows 9-10   no holes       the channel a DIP straddles: 0.3 inch from e to f
    rows 11-15  e d c b a      one clip per column
    rows 16-17  no holes
    row  18     - rail   (blue line above it)
    row  19     + rail   (red line below it)

A board without rails is rows 4 to 15 alone. A rail is holes in groups of five with one
column left out between groups, as many groups as the board is long, centred: on 63
columns that is columns 3 to 61, which is where the MB102's fifty holes are. On Fritzing's
half-size board the rail holes sit 0.37 of a pitch off the columns; a hole here is a grid
position, so they are put on the nearest column, which is columns 1 to 29. Nobody reads a
rail hole by its number -- a lead goes "into the + rail" -- so the rounding costs nothing a
builder can see, and it is written here so nobody mistakes it for a measurement.

Pure and deterministic, and it imports nothing but the model: ``geometry`` asks this
module which positions have no hole and what a hole is called, so it cannot ask anything
of ``geometry`` in return.
"""

from __future__ import annotations

import re
from typing import Literal

from .model import STANDARD_PITCH_MM, Board, HoleCoord, Mm

#: Rows on a breadboard with power rails, and on one without.
ROWS_WITH_RAILS = 20
ROWS_WITHOUT_RAILS = 12

#: Which of the four power rails a row is. "top" is the rail pair beside row j.
type Rail = Literal["top-", "top+", "bottom-", "bottom+"]

#: What each row is, top to bottom, on a board with rails: a strip letter, a rail, or
#: None where the grid has no hole. Read off the MB102 -- see the module docstring.
_ROWS_WITH_RAILS: tuple[str | None, ...] = (
    "top-", "top+", None, None,
    "j", "i", "h", "g", "f",
    None, None,
    "e", "d", "c", "b", "a",
    None, None, "bottom-", "bottom+",
)
_ROWS_WITHOUT_RAILS: tuple[str | None, ...] = _ROWS_WITH_RAILS[4:16]

#: Holes in one group of a rail, and the columns left out between groups.
RAIL_GROUP = 5

#: How long a breadboard is, in columns, and how many rows it has: the three sizes sold.
#: The mini has no rails. Keyed on what the board is called, which is its tie-point count.
SIZES: dict[str, tuple[int, int]] = {
    "170": (17, ROWS_WITHOUT_RAILS),
    "400": (30, ROWS_WITH_RAILS),
    "830": (63, ROWS_WITH_RAILS),
}

#: How thick the block is, clips and all. BusBoard gives its BB830 as 8.5 mm.
THICKNESS_MM: Mm = 8.5

#: The square opening a lead goes into, taken as a drill: a clip grips 0.3 to 0.8 mm leads.
#: ``wire-too-thick-for-hole`` reads it, which is the one rule that ever asks.
HOLE_MM: Mm = 0.8

#: A breadboard has no pad; the board model asks for one larger than the hole, and nothing
#: on a breadboard reads it.
_PAD_MM: Mm = 1.0

#: Plastic past the outermost hole centres beyond the usual half pitch, along the board
#: and across it. From BusBoard's 165.1 x 54.6 mm for the BB830 over the MB102's grid.
BORDER_X_MM: Mm = 2.54
BORDER_Y_MM: Mm = 1.9


def is_breadboard(board: Board) -> bool:
    return board.type == "breadboard"


def has_rails(board: Board) -> bool:
    return is_breadboard(board) and board.rows == ROWS_WITH_RAILS


def board_problem(board: Board) -> str | None:
    """Why this cannot be a breadboard, or None if it can.

    One answer for ``board.set`` and for loading a file, which refuse the same things for
    the same reason (CLAUDE.md, "persist.py refuses at load what board.set refuses").
    """
    if not is_breadboard(board):
        return None
    if board.rows not in (ROWS_WITH_RAILS, ROWS_WITHOUT_RAILS):
        return (
            f"A breadboard has {ROWS_WITH_RAILS} rows with power rails or "
            f"{ROWS_WITHOUT_RAILS} without; this one has {board.rows}."
        )
    if board.pitch != STANDARD_PITCH_MM:
        return f"A breadboard's holes are {STANDARD_PITCH_MM} mm apart; this one says {board.pitch}."
    if has_rails(board) and board.cols < RAIL_GROUP:
        return f"A breadboard with power rails needs at least {RAIL_GROUP} columns."
    return None


def breadboard(size: str) -> Board:
    """A breadboard of one of the ``SIZES``."""
    cols, rows = SIZES[size]
    return Board(
        type="breadboard",
        cols=cols,
        rows=rows,
        pitch=STANDARD_PITCH_MM,
        thickness=THICKNESS_MM,
        material="FR4",
        pad_diameter=_PAD_MM,
        drill_diameter=HOLE_MM,
        border_x_mm=BORDER_X_MM,
        border_y_mm=BORDER_Y_MM,
    )


def _roles(board: Board) -> tuple[str | None, ...]:
    return _ROWS_WITH_RAILS if board.rows == ROWS_WITH_RAILS else _ROWS_WITHOUT_RAILS


def _role(board: Board, row: int) -> str | None:
    roles = _roles(board)
    return roles[row] if 0 <= row < len(roles) else None


def rail_columns(board: Board) -> frozenset[int]:
    """The columns a rail has a hole in: groups of five, one column between, centred."""
    groups = (board.cols + 1) // (RAIL_GROUP + 1)
    span = groups * (RAIL_GROUP + 1) - 1
    first = (board.cols - span) // 2
    return frozenset(
        first + offset
        for offset in range(span)
        if offset % (RAIL_GROUP + 1) != RAIL_GROUP
    )


def strip_letter(board: Board, row: int) -> str | None:
    """The letter printed beside a row of clips, or None for a rail or a row with no holes."""
    role = _role(board, row)
    return role if role is not None and len(role) == 1 else None


def rail_of(board: Board, row: int) -> Rail | None:
    role = _role(board, row)
    if role in ("top-", "top+", "bottom-", "bottom+"):
        return role  # type: ignore[return-value]
    return None


def has_hole(board: Board, hole: HoleCoord) -> bool:
    """Whether there is a hole at this grid position at all."""
    if not (0 <= hole.col < board.cols and 0 <= hole.row < board.rows):
        return False
    if strip_letter(board, hole.row) is not None:
        return True
    return rail_of(board, hole.row) is not None and hole.col in rail_columns(board)


def missing_holes(board: Board) -> frozenset[HoleCoord]:
    """Every grid position on a breadboard that has no hole -- the channel, the gaps
    beside the rails, the columns a rail leaves out. Empty on any other board."""
    if not is_breadboard(board):
        return frozenset()
    return frozenset(
        HoleCoord(col, row)
        for row in range(board.rows)
        for col in range(board.cols)
        if not has_hole(board, HoleCoord(col, row))
    )


def clip_of(board: Board, hole: HoleCoord) -> tuple[int, int] | None:
    """The clip a hole is over, or None where there is no hole.

    A column's upper five holes are ``(col, 0)``, its lower five ``(col, 1)``, and a rail
    is ``(row, -1)``: unique on one board, which is all a segment id has to be.
    """
    if not is_breadboard(board) or not has_hole(board, hole):
        return None
    if rail_of(board, hole.row) is not None:
        return (hole.row, -1)
    letter = strip_letter(board, hole.row)
    return (hole.col, 0 if letter is not None and letter >= "f" else 1)


def clip_holes(board: Board, hole: HoleCoord) -> tuple[HoleCoord, ...]:
    """Every hole over the same clip as this one, itself included; empty where there is
    no hole."""
    clip = clip_of(board, hole)
    if clip is None:
        return ()
    if clip[1] == -1:
        return tuple(HoleCoord(col, hole.row) for col in sorted(rail_columns(board)))
    return tuple(
        HoleCoord(hole.col, row)
        for row in range(board.rows)
        if clip_of(board, HoleCoord(hole.col, row)) == clip
    )


# ---------------------------------------------------------------------------
# What a hole is called
# ---------------------------------------------------------------------------

#: How a rail hole is written: the rail, then the column it stands beside.
_RAIL_PREFIX: dict[Rail, str] = {"top-": "T-", "top+": "T+", "bottom-": "B-", "bottom+": "B+"}
_PREFIX_RAIL: dict[str, Rail] = {prefix: rail for rail, prefix in _RAIL_PREFIX.items()}

_STRIP_NAME = re.compile(r"([a-j])([1-9][0-9]*)")
_RAIL_NAME = re.compile(r"([TB][+-])([1-9][0-9]*)")


def hole_name(board: Board, hole: HoleCoord) -> str | None:
    """A hole as the breadboard itself prints it: "e12", or "T+12" on a rail.

    The letter is the row's and the number the column's, the way round every breadboard
    prints them -- the opposite of a perfboard address, and lower case, so the two can
    never be mistaken for each other. None where there is no hole.
    """
    if not has_hole(board, hole):
        return None
    rail = rail_of(board, hole.row)
    if rail is not None:
        return f"{_RAIL_PREFIX[rail]}{hole.col + 1}"
    return f"{strip_letter(board, hole.row)}{hole.col + 1}"


def parse_hole_name(board: Board, text: str) -> HoleCoord | None:
    """The inverse of :func:`hole_name`; None for anything that is not a hole here."""
    rows = _roles(board)
    found: HoleCoord | None = None
    if (match := _STRIP_NAME.fullmatch(text)) is not None:
        found = HoleCoord(int(match.group(2)) - 1, rows.index(match.group(1)))
    elif (match := _RAIL_NAME.fullmatch(text)) is not None:
        rail = _PREFIX_RAIL[match.group(1)]
        if rail not in rows:
            return None
        found = HoleCoord(int(match.group(2)) - 1, rows.index(rail))
    if found is None or not has_hole(board, found):
        return None
    return found

