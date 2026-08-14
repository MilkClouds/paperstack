import json
import sys

import pytest

from paperstack import cli, corpora, entry_types


def _corpus(path):
    target = path / "entries" / "papers" / "example2026paper.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        entry_types.render_scaffold(
            "paper",
            entry_id="arxiv:2601.00001",
            title="An Example Paper",
            editor="tester",
        )
    )
    return path


def _run(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["paperstack", *arguments])
    return cli.main()


def test_registry_add_use_list_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    local = tmp_path / "reviews"

    first = corpora.add("work", kind="path", location=str(local))
    corpora.add("team", kind="repo", location="example/papers")

    assert first.name == "work"
    assert corpora.active().name == "work"
    assert [item.name for item in corpora.entries()] == ["team", "work"]
    assert corpora.use("team").location == "example/papers"
    assert corpora.remove("team").name == "team"
    assert corpora.active().name == "work"
    assert json.loads(corpora.config_path().read_text())["version"] == 1


def test_registry_rejects_bad_names_repositories_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    with pytest.raises(corpora.ConfigError, match="corpus name"):
        corpora.add("Not Good", kind="repo", location="example/papers")
    with pytest.raises(corpora.ConfigError, match="OWNER/REPO"):
        corpora.add("bad", kind="repo", location="not-a-repository")

    corpora.config_path().parent.mkdir(parents=True)
    corpora.config_path().write_text("not json")
    with pytest.raises(corpora.ConfigError, match="unreadable"):
        corpora.load()


def test_cli_path_profile_becomes_active_and_resolves(tmp_path, monkeypatch, capsys):
    root = _corpus(tmp_path / "reviews")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PAPERSTACK_DIR", raising=False)
    monkeypatch.delenv("PAPERSTACK_REPO", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _run(monkeypatch, "corpus", "add", "work", "--path", str(root)) == 0
    assert _run(monkeypatch, "review", "show", "example2026paper", "--brief") == 0
    assert _run(monkeypatch, "corpus", "list", "--json") == 0

    output = capsys.readouterr().out
    assert "An Example Paper" in output
    assert '"active": true' in output


def test_cli_first_profile_is_active_and_use_switches_it(tmp_path, monkeypatch, capsys):
    first = _corpus(tmp_path / "first")
    second = _corpus(tmp_path / "second")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert _run(monkeypatch, "corpus", "add", "first", "--path", str(first)) == 0
    assert _run(monkeypatch, "corpus", "add", "second", "--path", str(second)) == 0
    assert corpora.active().name == "first"
    assert _run(monkeypatch, "corpus", "use", "second") == 0
    assert corpora.active().name == "second"
    assert "second" in capsys.readouterr().out
