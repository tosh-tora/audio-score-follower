"""Guards that keep the documentation set from decaying again.

Three failure modes have actually happened in this repository and none of
them was caught by anything:

1. CLAUDE.md grew to 402 lines. It is prepended to EVERY session, and
   Anthropic's guidance is "target under 200 lines … Longer files consume
   more context and reduce adherence" — so the safety notes written into
   it were themselves making instructions less likely to be followed.
2. Doc links went stale. Moving measurements into docs/ left 8 README
   links pointing at content that no longer existed there.
3. The same figure was maintained in three places and drifted: the
   mismatch calibration read 0.18 / 5.4s in one docstring and 0.20 / 5.0s
   in another.

CLAUDE.md already told us not to do these things — and we did them anyway.
Prose in a file Claude may or may not weigh is advisory; these tests are
not. Keep them cheap so they always run with the rest of the suite.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Anthropic's documented target for a CLAUDE.md file.
CLAUDE_MD_MAX_LINES = 200


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _markdown_files() -> list[Path]:
    files = [REPO / "CLAUDE.md", REPO / "README.md"]
    files += sorted((REPO / "docs").glob("*.md"))
    files += sorted((REPO / ".claude" / "rules").glob("*.md"))
    return [f for f in files if f.exists()]


# ---------------------------------------------------------------- size


def test_claude_md_stays_under_the_line_budget():
    """CLAUDE.md is loaded into every session; keep it small.

    If this fails, do not just delete lines. Move the content to where it
    belongs: a constraint about one code site goes in a comment at that
    site, an area convention goes in .claude/rules/ (loaded only when
    Claude reads a matching file), and design prose goes in docs/.
    """
    lines = len(_read("CLAUDE.md").splitlines())
    assert lines <= CLAUDE_MD_MAX_LINES, (
        f"CLAUDE.md is {lines} lines, over the {CLAUDE_MD_MAX_LINES}-line target. "
        "It loads in every session, so length here costs context and reduces "
        "adherence to the rules it contains."
    )


# --------------------------------------------------------------- links


def _slug(heading: str) -> str:
    """Approximate GitHub's heading -> anchor conversion."""
    text = heading.replace("`", "").lower().strip()
    kept = []
    for ch in text:
        if ch in "-_" or ch.isalnum() or ch.isspace():
            kept.append(ch)
        elif unicodedata.category(ch)[0] in "PS":
            continue
        else:
            kept.append(ch)
    return re.sub(r"\s", "-", "".join(kept))


def _anchors(text: str) -> set[str]:
    return {_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*)$", text, re.M)}


@pytest.mark.parametrize("md", _markdown_files(), ids=lambda p: p.name)
def test_internal_doc_links_resolve(md: Path):
    """Every relative markdown link (and #anchor) points at something real."""
    body = re.sub(r"```.*?```", "", md.read_text(encoding="utf-8"), flags=re.S)
    problems = []
    for match in re.finditer(r"\]\(([^)]+)\)", body):
        link = match.group(1)
        if link.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, fragment = link.partition("#")
        target = (md.parent / path).resolve() if path else md
        # A sibling repository referenced for context is out of scope here.
        if not str(target).startswith(str(REPO)):
            continue
        if not target.exists():
            problems.append(f"{link} -> missing file")
            continue
        if fragment and target.suffix == ".md":
            if fragment not in _anchors(target.read_text(encoding="utf-8")):
                problems.append(f"{link} -> missing anchor")
    assert not problems, f"{md.relative_to(REPO)} has broken links: {problems}"


# ----------------------------------------------------- single source of truth


def test_mismatch_calibration_matches_the_shipped_defaults():
    """The documented calibration must describe the values we actually ship.

    These drifted apart once already. Whichever side you change, change
    both — docs/calibration.md is the single source of truth for the
    numbers, and the constructor defaults are what actually runs.
    """
    from audio_score_follower.core.oltw_follower import OnlineDTWFollower

    import inspect

    params = inspect.signature(OnlineDTWFollower.__init__).parameters
    threshold = params["mismatch_cost_threshold"].default
    seconds = params["mismatch_seconds"].default

    calibration = _read("docs/calibration.md")
    assert f"{threshold}" in calibration, (
        f"mismatch_cost_threshold default is {threshold} but that value does not "
        "appear in docs/calibration.md — the calibration record has drifted from "
        "what ships."
    )
    assert f"{seconds:g}s" in calibration, (
        f"mismatch_seconds default is {seconds} but '{seconds:g}s' does not appear "
        "in docs/calibration.md."
    )


# -------------------------------------------------------- constraint survival

# Constraints that are load-bearing but have no direct regression test of
# their own — documentation is their only safety net. Each needle is a
# short distinctive phrase from wherever the constraint is recorded.
#
# If one of these fails: check WHY the text is gone. If you deliberately
# rephrased it, update the needle here. If the constraint itself was
# dropped, that is what this test exists to catch.
UNTESTED_CONSTRAINTS = {
    "_advance_inertia must not write _current_ref_pos": "Does NOT touch ``_current_ref_pos``",
    "low confidence alone must not trigger inertia": "Low DP confidence alone is NOT enough",
    "unfreeze must not clear _inertia_active immediately": "keeps running until",
    "global rematch stays suppressed during inertia": "teleport on top of inertia",
    "rapid reset must reseed D_prev": "The reseed is load-bearing",
    "pre-lock-in unfreeze must reseed the DP": "The reseed is essential",
    "seek() from freeze()/unfreeze() deadlocks": "would deadlock",
    "internal confidence scale is calibrated, not cosmetic": "this is the INTERNAL",
    "fusion-disabled cost passes through raw cosine": "Multiplying by chroma_weight here",
    "score_bpm resolution belongs to the CLI": "belongs to the CLI in cli/build_reference.py",
    "AppState fields need a set_xxx() mutator": "give it a single ``set_xxx()`` mutator",
    "silence-measure button is never source-linked": "linked disabling confused operators",
    "synth detection order lives in one file": "検出順を変えるときはこのファイル",
}


def _searchable_corpus() -> str:
    """All prose we record constraints in, with comment markers flattened."""
    chunks = [md.read_text(encoding="utf-8") for md in _markdown_files()]
    for src in sorted((REPO / "audio_score_follower").rglob("*.py")):
        chunks.append(src.read_text(encoding="utf-8"))
    joined = "\n".join(chunks)
    # A needle may span a line break with a '#' in between.
    joined = re.sub(r"^\s*#\s?", " ", joined, flags=re.M)
    return re.sub(r"\s+", " ", joined)


@pytest.mark.parametrize(
    "name,needle", sorted(UNTESTED_CONSTRAINTS.items()), ids=lambda v: v if " " in str(v) else ""
)
def test_constraint_is_still_written_down_somewhere(name: str, needle: str):
    corpus = _searchable_corpus()
    assert re.sub(r"\s+", " ", needle).strip() in corpus, (
        f"The only record of this constraint is gone: {name!r}. "
        "It has no regression test, so this text was its safety net. "
        "Restore it, or update the needle in UNTESTED_CONSTRAINTS if you "
        "only rephrased it."
    )
