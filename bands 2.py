"""Manufacturer wireless band designations, as scannable frequency ranges.

WHY A BAND IS A *LIST* OF RANGES
--------------------------------
After the US 600 MHz repack (completed 2020) the FCC auctioned 617–652 and
663–698 MHz to mobile carriers. Wireless mics kept 470–608 MHz plus the
2 MHz duplex gap at 614–616. So a band like Shure J8 — originally a clean
554–626 MHz — is now literally two disjoint pieces: 554–608 and 614–616.
That is why `Band.ranges` is a list, and why the scanner takes a list of
spans rather than one start/end pair.

ACCURACY
--------
Compiled 2026-08-01 from Shure and Sennheiser published figures (see
SOURCES at the bottom). Shure's master band chart PDF was not machine-
readable, so this covers the families that could be confirmed from primary
or multiple independent sources — it is NOT every band Shure has shipped.
Band letters mean different ranges on different product lines (Shure H5 on
SLX is not the same spectrum as H50 on ULX-D), which is why `family` is
part of every entry's identity.

**Verify against your own gear before coordinating a show.** Adding or
correcting an entry is a one-line edit to BANDS below.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# US wireless-mic spectrum after the 600 MHz repack: the UHF TV band, plus
# the duplex gap. Everything above 616 MHz is mobile carrier spectrum now.
US_USABLE: list[tuple[float, float]] = [(470.0, 608.0), (614.0, 616.0)]


@dataclass(frozen=True)
class Band:
    name: str                       # manufacturer designation, e.g. "H5"
    maker: str                      # "Shure" | "Sennheiser"
    family: str                     # product line the name belongs to
    ranges: tuple[tuple[float, float], ...]   # MHz spans, ascending
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.maker} {self.family} {self.name}"

    @property
    def span_text(self) -> str:
        return ", ".join(f"{a:g}–{b:g}" for a, b in self.ranges) + " MHz"

    @property
    def us_legal(self) -> str:
        """'full' | 'partial' | 'none' — how much survives the US repack."""
        kept = intersect(list(self.ranges), US_USABLE)
        if not kept:
            return "none"
        width = sum(b - a for a, b in self.ranges)
        return "full" if abs(sum(b - a for a, b in kept) - width) < 0.001 else "partial"


# ─────────────────────────────────────────────────────────────── range math ──

def merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort and union overlapping/touching spans. Picking G57 and H5 (which
    overlap heavily) must scan the union once, not the same MHz twice."""
    if not ranges:
        return []
    out: list[tuple[float, float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in ranges):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def intersect(ranges: list[tuple[float, float]],
              mask: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The parts of `ranges` that fall inside `mask`."""
    out: list[tuple[float, float]] = []
    for a, b in merge(ranges):
        for m_a, m_b in mask:
            lo, hi = max(a, m_a), min(b, m_b)
            if hi - lo > 0.0001:
                out.append((lo, hi))
    return merge(out)


def total_width(ranges: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in merge(ranges))


# ──────────────────────────────────────────────────────────────── the table ──
# Ranges are the manufacturer's published tuning range, trimmed where the
# repack removed spectrum (originals noted).

BANDS: list[Band] = [
    # ── Shure ULX-D / QLX-D ──────────────────────────────────────────────
    Band("G50", "Shure", "ULX-D/QLX-D", ((470.0, 534.0),)),
    Band("H50", "Shure", "ULX-D/QLX-D", ((534.0, 598.0),)),
    Band("J50", "Shure", "ULX-D/QLX-D", ((572.0, 608.0), (614.0, 616.0)),
         "orig. 572–636; repack trimmed"),
    Band("L50", "Shure", "ULX-D/QLX-D", ((632.0, 698.0),),
         "600 MHz band — not usable in the US post-repack"),

    # ── Shure Axient Digital ─────────────────────────────────────────────
    Band("G57", "Shure", "Axient Digital", ((470.0, 608.0), (614.0, 616.0)),
         "orig. 470–616; repack trimmed"),
    Band("G56", "Shure", "Axient Digital", ((470.0, 608.0), (614.0, 616.0)),
         "orig. 470–636; repack trimmed"),
    Band("K53", "Shure", "Axient Digital", ((606.0, 698.0),),
         "600 MHz band — not usable in the US post-repack"),

    # ── Shure UHF-R / legacy wideband ────────────────────────────────────
    Band("J8", "Shure", "UHF-R", ((554.0, 608.0), (614.0, 616.0)),
         "orig. 554–626; J8A is the post-repack version"),

    # ── Shure SLX (analog) ───────────────────────────────────────────────
    Band("G4E", "Shure", "SLX", ((470.0, 494.0),)),
    Band("G5E", "Shure", "SLX", ((494.0, 518.0),)),
    Band("H5",  "Shure", "SLX", ((518.0, 542.0),)),
    Band("J3",  "Shure", "SLX", ((572.0, 596.0),)),
    Band("L4",  "Shure", "SLX", ((638.0, 662.0),),
         "600 MHz band — not usable in the US post-repack"),
    Band("P4",  "Shure", "SLX", ((702.0, 726.0),),
         "700 MHz — illegal for wireless mics in the US since 2010"),
    Band("R5",  "Shure", "SLX", ((800.0, 820.0),), "not US wireless-mic spectrum"),
    Band("S6",  "Shure", "SLX", ((838.0, 865.0),), "not US wireless-mic spectrum"),

    # ── Shure BLX ────────────────────────────────────────────────────────
    Band("J10", "Shure", "BLX", ((584.0, 608.0),), "orig. 584–608"),

    # ── Sennheiser evolution wireless G3/G4 ──────────────────────────────
    Band("A1", "Sennheiser", "ew G3/G4", ((470.0, 516.0),)),
    Band("A",  "Sennheiser", "ew G3/G4", ((516.0, 558.0),)),
    Band("AS", "Sennheiser", "ew G4",    ((520.0, 558.0),)),
    Band("G",  "Sennheiser", "ew G3/G4", ((566.0, 608.0),)),
    Band("GB", "Sennheiser", "ew G3/G4", ((606.0, 648.0),),
         "600 MHz band — not usable in the US post-repack"),
    Band("B",  "Sennheiser", "ew G3/G4", ((626.0, 668.0),),
         "600 MHz band — not usable in the US post-repack"),
    Band("C",  "Sennheiser", "ew G3/G4", ((734.0, 776.0),), "not US spectrum"),
    Band("D",  "Sennheiser", "ew G3/G4", ((780.0, 822.0),), "not US spectrum"),
    Band("E",  "Sennheiser", "ew G3/G4", ((823.0, 865.0),), "not US spectrum"),
]

BY_LABEL: dict[str, Band] = {b.label: b for b in BANDS}


def us_bands() -> list[Band]:
    """Bands with any spectrum still usable in the US."""
    return [b for b in BANDS if b.us_legal != "none"]


def ranges_for(labels) -> list[tuple[float, float]]:
    """Selected band labels -> merged scannable spans."""
    picked: list[tuple[float, float]] = []
    for lab in labels:
        band = BY_LABEL.get(lab)
        if band:
            picked.extend(band.ranges)
    return merge(picked)


# Legacy single-span quick picks, kept for the plain preset dropdown.
FREQ_PRESETS: dict[str, tuple[float, float] | None] = {
    "Sub-GHz  470–900 MHz": (470.0, 900.0),
    "US mic   470–608 MHz": (470.0, 608.0),
    "UHF      470–698 MHz": (470.0, 698.0),
    "UHF      470–608 MHz": (470.0, 608.0),
    "VHF      174–216 MHz": (174.0, 216.0),
    "Custom":               None,
}


if __name__ == "__main__":
    assert merge([(470, 534), (518, 542)]) == [(470.0, 542.0)]           # overlap
    assert merge([(470, 494), (494, 518)]) == [(470.0, 518.0)]           # touching
    assert merge([(554, 608), (614, 616)]) == [(554.0, 608.0), (614.0, 616.0)]
    assert intersect([(470, 698)], US_USABLE) == [(470.0, 608.0), (614.0, 616.0)]
    assert BY_LABEL["Shure SLX H5"].us_legal == "full"
    assert BY_LABEL["Shure ULX-D/QLX-D L50"].us_legal == "none"
    assert BY_LABEL["Sennheiser ew G3/G4 GB"].us_legal == "partial"      # 606–608 survives
    assert ranges_for(["Shure SLX H5", "Shure SLX G5E"]) == [(494.0, 542.0)]
    print(f"bands self-test: OK ({len(BANDS)} bands, {len(us_bands())} usable in US)")

# SOURCES (fetched 2026-08-01)
#   Shure G50/H50/J50/L50 ..... shure.com "ULX-D Digital Wireless in the H50 Band: FAQs"
#   Shure G56/G57/K53/J8 ...... Shure US frequency-band listings
#   Shure SLX bands ........... Shure SLX frequency compatibility chart + retailer specs
#   Sennheiser G3/G4 bands .... published G3/G4 band tables
#   US repack spectrum ........ FCC 600 MHz transition: mics keep 470–608 + 614–616
