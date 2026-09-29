"""A footprint's name in the interface's language.

The engine names every footprint in English -- "Resistor (axial, 3-hole span)" -- and
keeps it: the names are in ``footprints.expected.json``, which the port reproduces byte
for byte, and in every ``.perf`` a generated part's name is derived from. The engine's
``phrasebook`` recognises its own phrasings and re-says them, numbers and all, and this
module asks it to in the window's language -- ONE table for the Parts panel and the build
guide, so a part is called one thing in the list it was picked from and in the step that
fits it.

A name no template recognises is shown as it is, which is also what a name typed by
somebody is: a half-translated parts list is usable, and a part that vanished because its
name did not parse is not.
"""

from __future__ import annotations

from perfboard_studio.phrasebook import guide_language, phrasebook

from .i18n import language


def footprint_label(name: str) -> str:
    """``name`` as the interface should show it. Unchanged in English, by construction."""
    return phrasebook(guide_language(language())).footprint(name)


__all__ = ["footprint_label"]
