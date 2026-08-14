from pathlib import Path

from paperstack import entry_types


def _corpus(tmp_path: Path) -> Path:
    for contract in entry_types.ENTRY_TYPES.values():
        (tmp_path / "entries" / contract.directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "README.md").write_text("# Test\n")
    return tmp_path


def test_scaffolds_follow_their_contracts(tmp_path):
    root = _corpus(tmp_path)
    arguments = {
        "paper": {},
        "talk": {"speaker": ["A Speaker"], "channel": "A Channel", "published": "2025-01-01"},
        "post": {"publisher": "A Publisher", "published": "2025-01-01"},
    }
    for kind, extra in arguments.items():
        contract = entry_types.ENTRY_TYPES[kind]
        path = root / "entries" / contract.directory / f"test2025{kind}.md"
        path.write_text(
            entry_types.render_scaffold(
                kind,
                entry_id="https://example.test/source",
                title=f"A Test {kind.title()}",
                editor="tester",
                **extra,
            )
        )

    diagnostics, count = entry_types.validate(root)

    assert count == 3
    assert not [item for item in diagnostics if item.level == "ERROR"]
    assert "quality:" not in (root / "entries" / "talks" / "test2025talk.md").read_text()
    assert "quality:" not in (root / "entries" / "posts" / "test2025post.md").read_text()


def test_validation_rejects_paper_quality_on_a_talk(tmp_path):
    root = _corpus(tmp_path)
    path = root / "entries" / "talks" / "test2025talk.md"
    text = entry_types.render_scaffold(
        "talk",
        entry_id="https://example.test/talk",
        title="A Test Talk",
        editor="tester",
        speaker=["A Speaker"],
        channel="A Channel",
        published="2025-01-01",
    ).replace("tags: []", "quality: good\ntags: []")
    path.write_text(text)

    diagnostics, _ = entry_types.validate(root)

    assert any(item.message == "talk must not carry paper quality" for item in diagnostics)


def test_talk_scaffold_preserves_multiple_speakers():
    text = entry_types.render_scaffold(
        "talk",
        entry_id="https://example.test/talk",
        title="A Test Talk",
        editor="tester",
        speaker=["First Speaker", "Second Speaker"],
        channel="A Channel",
        published="2025-01-01",
    )

    assert 'speaker: ["First Speaker", "Second Speaker"]' in text


def test_entry_links_resolve_relative_to_the_authored_file(tmp_path):
    root = _corpus(tmp_path)
    talk = root / "entries" / "talks" / "linked2025talk.md"
    talk.write_text(
        entry_types.render_scaffold(
            "talk",
            entry_id="https://example.test/talk",
            title="A Linked Talk",
            editor="tester",
            speaker=["A Speaker"],
            channel="A Channel",
            published="2025-01-01",
        )
    )
    paper = root / "entries" / "papers" / "source2025paper.md"
    paper.write_text(
        entry_types.render_scaffold(
            "paper",
            entry_id="https://example.test/paper",
            title="A Source Paper",
            editor="tester",
        ).replace("**One-liner.** TODO", "**One-liner.** TODO\n\n[wrong relative path](linked2025talk.md)")
    )

    diagnostics, _ = entry_types.validate(root)

    assert any(item.message == "link to a nonexistent entry: linked2025talk.md" for item in diagnostics)


def test_readme_rejects_flat_entry_links(tmp_path):
    root = _corpus(tmp_path)
    (root / "README.md").write_text("[old path](entries/old2025paper.md)\n")

    diagnostics, _ = entry_types.validate(root)

    assert any(item.message == "unsupported entry path: entries/old2025paper.md" for item in diagnostics)


def test_invalid_linked_frontmatter_is_reported_without_crashing(tmp_path):
    root = _corpus(tmp_path)
    path = root / "entries" / "papers" / "broken2025paper.md"
    path.write_text('---\ntitle: "unterminated\n---\n# Broken\n')
    (root / "README.md").write_text("[broken](entries/papers/broken2025paper.md)\n")

    diagnostics, count = entry_types.validate(root)

    assert count == 1
    assert any(item.message == "invalid frontmatter: entries/papers/broken2025paper.md" for item in diagnostics)
