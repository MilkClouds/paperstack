import io
import json
import sys
import tarfile

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


def test_cli_init_bootstraps_an_empty_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "empty"

    assert _run(monkeypatch, "corpus", "init", "work", "--path", str(root)) == 0
    assert corpora.active().location == str(root)
    assert json.loads((root / "entries" / "collections.json").read_text())["collections"] == []


def test_remove_can_purge_a_repo_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    corpora.add("private", kind="repo", location="example/papers")
    cache = cli.RemoteCache("example/papers")
    cache.root.mkdir(parents=True)
    (cache.root / "cached").write_text("value")

    assert _run(monkeypatch, "corpus", "remove", "private", "--purge-cache", "--yes") == 0
    assert not cache.root.exists()


def test_remote_cache_keys_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cli.RemoteCache("a_b/c").root != cli.RemoteCache("a/b_c").root


def test_failed_cache_publish_restores_the_previous_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    cache = cli.RemoteCache("example/private")
    (cache.corpus / "entries").mkdir(parents=True)
    (cache.corpus / "old").write_text("value")
    staged = tmp_path / "staged"
    (staged / "entries").mkdir(parents=True)
    replace = cli.os.replace

    def fail_new(source, destination):
        if source == staged and destination == cache.corpus:
            raise OSError("injected")
        return replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", fail_new)
    assert cli.install(cache, staged) is False
    assert (cache.corpus / "old").read_text() == "value"


def test_remote_cache_keeps_entries_only(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    source = _corpus(tmp_path / "source")
    (source / "secret.txt").write_text("private")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        bundle.add(source, arcname="repo")
    monkeypatch.setattr(cli, "remote_sha", lambda repo: "a" * 40)
    monkeypatch.setattr(cli, "gh", lambda *args, **kwargs: archive.getvalue())

    assert cli.sync("example/private") is True
    cached = cli.RemoteCache("example/private").corpus
    assert (cached / "entries" / "papers" / "example2026paper.md").is_file()
    assert not (cached / "secret.txt").exists()
