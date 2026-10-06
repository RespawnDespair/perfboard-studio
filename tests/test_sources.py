"""What reading the source catches that running it might not.

The same idea as ``test_gl.test_every_vtk_touching_test_is_marked`` and the i18n scanner:
a mistake with a recognisable SHAPE is found by its shape, everywhere at once, rather than
by somebody happening to look at the one screen it spoils.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "perfboard_studio"


def test_no_plain_literal_glued_onto_an_f_string_keeps_a_doubled_brace() -> None:
    """``f"a {{ {x}; " "b }}"`` prints ``b }}``: the second literal is not an f-string, so
    its braces are not halved. That is how the welcome window's first button handed Qt a
    stylesheet with one brace too many, and Qt refused the whole of it -- in silence, as
    far as anybody looking at the window could tell.

    Inside a parsed f-string every doubled brace has already been halved, so a constant
    part of one that still holds ``{{`` or ``}}`` can only have come from a plain literal
    concatenated onto it.
    """
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            for part in node.values:
                if isinstance(part, ast.Constant) and ("{{" in part.value or "}}" in part.value):
                    found.append(f"{path.relative_to(SRC)}:{node.lineno}: {part.value!r}")
    assert not found, "\n".join(found)
