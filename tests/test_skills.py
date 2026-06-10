"""Skills runtime tests (V2 §2.F). Pure file IO — no DB."""

from __future__ import annotations

from bogi.modules import skills


def test_frontmatter_parse_basic():
    text = "---\nname: foo\ndescription: bar baz\n---\nbody line 1\nbody line 2"
    meta, body = skills._parse_frontmatter(text)
    assert meta["name"] == "foo"
    assert meta["description"] == "bar baz"
    assert body == "body line 1\nbody line 2"


def test_frontmatter_parse_no_frontmatter():
    text = "just a body, no fences"
    meta, body = skills._parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_list_skills_finds_seeds():
    found = skills.list_skills()
    slugs = {s["slug"] for s in found}
    # Seed skills committed in skills/ must be discovered.
    assert "university-email-bg" in slugs
    assert "fmi-deadline-triage" in slugs
    # every entry has a non-empty description
    for s in found:
        assert s["description"], f"skill {s['slug']} has empty description"


def test_catalog_is_compact_lines():
    cat = skills.skills_catalog()
    assert "university-email-bg" in cat
    # one line per skill, "- name: description" shape
    assert all(line.startswith("- ") for line in cat.splitlines() if line.strip())


def test_read_skill_by_slug_returns_body():
    body = skills.read_skill("university-email-bg")
    assert "имейл" in body.lower()
    assert "vault/inbox" in body  # safety reminder present


def test_read_skill_missing_is_graceful():
    out = skills.read_skill("does-not-exist-xyz")
    assert "Няма skill" in out
    assert "does-not-exist-xyz" in out
