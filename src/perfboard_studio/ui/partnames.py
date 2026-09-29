"""A footprint's name in the interface's language.

The engine names every footprint in English -- "Resistor (axial, 3-hole span)" -- and
keeps it: the names are in ``footprints.expected.json``, which the port reproduces byte
for byte, and in every ``.perf`` a generated part's name is derived from. So the engine
does not translate them and this module does not ask it to. It reads the name the engine
wrote, recognises which of the engine's own phrasings it is, and says the same thing in
the window's language, numbers and all.

A name no template recognises is shown as it is, which is also what a name typed by
somebody is: a half-translated parts list is usable, and a part that vanished because its
name did not parse is not. ``tests/test_i18n.py`` holds every standard footprint to a
template, so a footprint added to the library in new words is noticed rather than
quietly left in English.
"""

from __future__ import annotations

import re
from functools import cache

from .i18n import t

#: The engine's phrasings, with each number or size as a numbered field. Written out as
#: ``footprints.py`` writes them, so the English column of every template is a name that
#: file can produce; the most specific first where one is a prefix of another.
NAME_TEMPLATES: tuple[str, ...] = (
    "Resistor (axial, {0}-hole span)",
    "Axial ({0}-hole span, {1} mm body)",
    "Diode, {0}",
    "Electrolytic capacitor, {0} mm dia, {1}-hole pitch",
    "Disc ceramic capacitor, {0} mm dia, {1}-hole pitch",
    "Disc ceramic capacitor, {0}-hole pitch",
    "Film capacitor, {0} mm, {1}-hole pitch",
    "Film capacitor, {0}-hole pitch",
    'DIP-{0} (0.6" wide)',
    "TO-92 (inline, on-grid)",
    "LED, {0} mm round",
    "Pin header, {0}",
    "IDC box header, {0}",
    "Screw terminal, {0}-way, 5.08 mm pitch, vertical (wires from above)",
    "Screw terminal, {0}-way, 5.08 mm pitch",
    "Screw terminal, {0}-way",
    "Potentiometer, 3-pin inline",
    "Tactile switch, 4-pin",
    "Tactile switch, {0} mm",
    "Crystal, HC-49/U",
    "Small SPDT relay",
    "Custom part, {0} pins, {1} mm, body offset {2} mm",
    "Custom part, {0} pins, {1} mm",
    "Module, {0} pins, {1} mm, seated {2} mm up",
)

#: What a field may hold: a number, a size like "15x10x8" or "5 x 3", an offset pair
#: "-2, 1.5", a package like "DO-41". Never a whole clause -- a field that could swallow
#: ", 5.08 mm pitch" would let a short template claim a longer name.
_FIELD = r"([-0-9A-Za-z.]+(?:\s?x\s?[-0-9.]+)*(?:, [-0-9.]+)?)"


@cache
def _pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"\{\d\}", template)
    return re.compile(_FIELD.join(re.escape(part) for part in parts))


def matching_template(name: str) -> str | None:
    """Which of the engine's phrasings ``name`` is, or None."""
    for template in NAME_TEMPLATES:
        if _pattern(template).fullmatch(name):
            return template
    return None


def footprint_label(name: str) -> str:
    """``name`` as the interface should show it. Unchanged in English, by construction."""
    template = matching_template(name)
    if template is None:
        return name
    fields = _pattern(template).fullmatch(name)
    assert fields is not None
    return t(template).format(*fields.groups())


__all__ = ["NAME_TEMPLATES", "footprint_label", "matching_template"]
