import stat

from paperstack import remote_corpus


def test_cache_root_is_private_and_lock_is_not_world_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = remote_corpus.RemoteCache("example/private")
    with remote_corpus.cache_lock(cache) as mine:
        assert mine is True
    assert stat.S_IMODE(cache.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(cache.lock.stat().st_mode) == 0o600


def test_legacy_cache_without_bound_manifest_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = remote_corpus.RemoteCache("example/private")
    (cache.corpus / "entries").mkdir(parents=True)
    (cache.corpus / remote_corpus.SHA_FILE).write_text("a" * 40)
    assert remote_corpus.cache_valid(cache) is False


def test_failed_publish_restores_the_previous_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    cache = remote_corpus.RemoteCache("example/private")
    (cache.corpus / "entries").mkdir(parents=True)
    (cache.corpus / "old.txt").write_text("old")
    new = tmp_path / "new"
    (new / "entries").mkdir(parents=True)
    original = remote_corpus.os.replace

    def fail_new(source, destination):
        if source == new and destination == cache.corpus:
            raise OSError("injected publish failure")
        return original(source, destination)

    monkeypatch.setattr(remote_corpus.os, "replace", fail_new)
    assert remote_corpus.install(cache, new) is False
    assert (cache.corpus / "old.txt").read_text() == "old"
