from __future__ import annotations

import hashlib

import polars as pl
import pytest

from paperstack import dblp_build, dblp_index
from paperstack.dblp_catalog import toc_query, years

BIBTEX = """@inproceedings{chi2023diffusion,
  author = {Cheng Chi and others},
  title = {Diffusion Policy: Visuomotor Policy Learning via Action Diffusion},
  booktitle = {Robotics: Science and Systems},
  year = {2023},
  doi = {10.15607/RSS.2023.XIX.026}
}"""


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_fixture(
    path,
    *,
    snapshot="fixture",
    source=None,
    declared_records="1",
    title=None,
):
    path.parent.mkdir(parents=True, exist_ok=True)
    source = source or path.resolve().as_uri()
    bibtex = (
        BIBTEX
        if title is None
        else BIBTEX.replace("Diffusion Policy: Visuomotor Policy Learning via Action Diffusion", title)
    )
    frame = pl.DataFrame([dblp_index._row("", bibtex)], schema=dblp_index.SCHEMA, orient="row")
    frame.write_parquet(
        path,
        metadata={
            "snapshot": snapshot,
            "source": source,
            "records": declared_records,
            "minimum_records": "1",
            "coverage": "selected CS venues",
            "schema_version": dblp_index.SCHEMA_VERSION,
        },
    )
    return source


def _snapshot(version, path, *, sha256=None):
    return dblp_index.Snapshot(version, path.resolve().as_uri(), sha256 or _digest(path), minimum_records=1)


def _install_fixture(monkeypatch, tmp_path, *, snapshot="fixture"):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    source = tmp_path / f"{snapshot}.parquet"
    _create_fixture(source, snapshot=snapshot)
    dblp_index.install(snapshot=_snapshot(snapshot, source))
    return source


def test_searches_parquet_snapshot(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path)

    matches = dblp_index.search("Diffusion Policy")

    assert len(matches) == 1
    assert matches[0]["dblp_key"] == "chi2023diffusion"
    assert matches[0]["venue"] == "Robotics: Science and Systems"
    assert "bibtex" not in matches[0]
    assert dblp_index.lookup(key="chi2023diffusion")[0]["doi"] == "10.15607/RSS.2023.XIX.026"
    assert dblp_index.lookup(doi="10.15607/rss.2023.xix.026")[0]["dblp_key"] == "chi2023diffusion"
    assert dblp_index.status()["snapshot"] == "fixture"


def test_installs_locally_built_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    source = tmp_path / "built.parquet"
    _create_fixture(source, snapshot="2026.08-live")

    result = dblp_index.install_file(source)

    assert result["installed"] is True
    assert result["records"] == 1
    assert dblp_index.status()["sha256"] == _digest(source)
    assert dblp_index.search("Diffusion Policy")


def test_batch_search_preserves_input_order(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path)

    matches = dblp_index.search_many(["missing", "Diffusion Policy"])

    assert matches[0] == []
    assert matches[1][0]["dblp_key"] == "chi2023diffusion"


def test_normalization_keeps_unicode_letters():
    assert dblp_index.normalize_title("π0: A Vision-Language-Action Model") == "π0avisionlanguageactionmodel"


def test_bibtex_fields_keep_nested_braces():
    bibtex = r"""@inproceedings{DBLP:conf/icml/Example18,
  title = {An {Example} Paper},
  booktitle = {Proceedings in Stockholmsm{\"{a}}ssan, Sweden},
  year = {2018}
}"""

    row = dblp_index._row("", bibtex)

    assert row[1] == "An Example Paper"
    assert row[4] == r"Proceedings in Stockholmsm{\"{a}}ssan, Sweden"
    assert row[8] == "https://dblp.org/rec/conf/icml/Example18.html"
    assert len(row) == len(dblp_index.COLUMNS)


def test_repository_owned_catalog_handles_year_rules_and_journals():
    assert years("iccv")[-2:] == [2023, 2025]
    assert years("neurips")[-1] == 2026
    assert toc_query("neurips", 2025) == "toc:db/conf/nips/nips2025.bht:"
    assert toc_query("sigmod", 2026, "c") == "toc:db/conf/sigmod/sigmod2026c.bht:"
    assert toc_query("tacl", 2026) == "toc:db/journals/tacl/tacl14.bht:"


def test_fetch_venue_year_paginates_dblp_bibtex():
    calls = []

    def get(_url, params):
        calls.append(params)
        if params["f"] == "0":
            return BIBTEX
        return ""

    entries = dblp_build.fetch_venue_year("rss", 2023, get=get)

    assert entries == [BIBTEX]
    assert [call["f"] for call in calls] == ["0", "100"]
    assert calls[0]["q"] == "toc:db/conf/rss/rss2023.bht:"


def test_fetch_venue_year_falls_back_to_venue_and_year():
    calls = []

    def get(_url, params):
        calls.append(params["q"])
        return BIBTEX if params["q"] == "venue:NeurIPS: year:2025:" and params["f"] == "0" else ""

    entries = dblp_build.fetch_venue_year("neurips", 2025, get=get)

    assert entries == [BIBTEX]
    assert calls[:2] == ["toc:db/conf/nips/nips2025.bht:", "venue:NeurIPS: year:2025:"]


def test_direct_builder_merges_refreshed_records_into_base(tmp_path):
    base = tmp_path / "base.parquet"
    _create_fixture(base, snapshot="2026.08")
    output = tmp_path / "refreshed.parquet"
    new_bibtex = (
        BIBTEX.replace("chi2023diffusion", "DBLP:conf/rss/New26")
        .replace("Diffusion Policy: Visuomotor Policy Learning via Action Diffusion", "A New RSS Paper")
        .replace("2023", "2026")
    )

    def fetch(venue, year, **_kwargs):
        return [new_bibtex] if (venue, year) == ("rss", 2026) else []

    result = dblp_build.build(
        output,
        snapshot="2026.09",
        base=base,
        selected_venues=["rss"],
        selected_years=[2026],
        minimum_records=1,
        fetch=fetch,
    )

    metadata = pl.read_parquet_metadata(output)
    frame = pl.read_parquet(output)
    assert result["records"] == 2
    assert frame["title"].to_list() == [
        "A New RSS Paper",
        "Diffusion Policy: Visuomotor Policy Learning via Action Diffusion",
    ]
    assert metadata["snapshot"] == "2026.09"
    assert metadata["venue_catalog"] == '{"rss":{"2026":1}}'


def test_direct_builder_replaces_a_successfully_refetched_venue_year(tmp_path):
    base = tmp_path / "base.parquet"
    _create_fixture(base, snapshot="2026.08")
    output = tmp_path / "refreshed.parquet"
    corrected = BIBTEX.replace(
        "Diffusion Policy: Visuomotor Policy Learning via Action Diffusion", "Corrected Diffusion Policy"
    )

    def fetch(venue, year, **_kwargs):
        return [corrected] if (venue, year) == ("rss", 2023) else []

    result = dblp_build.build(
        output,
        snapshot="2026.09",
        base=base,
        selected_venues=["rss"],
        selected_years=[2023],
        minimum_records=1,
        fetch=fetch,
    )

    assert result["records"] == 1
    assert pl.read_parquet(output)["title"].to_list() == ["Corrected Diffusion Policy"]


def test_direct_builder_preserves_base_when_dblp_has_not_ingested_year(tmp_path):
    base = tmp_path / "base.parquet"
    _create_fixture(base, snapshot="2026.08")
    output = tmp_path / "refreshed.parquet"

    result = dblp_build.build(
        output,
        snapshot="2026.09",
        base=base,
        selected_venues=["rss"],
        selected_years=[2026],
        minimum_records=1,
        fetch=lambda *_args, **_kwargs: [],
    )

    assert result["records"] == 1
    assert pl.read_parquet(output)["title"].to_list() == [
        "Diffusion Policy: Visuomotor Policy Learning via Action Diffusion"
    ]


def test_direct_builder_rejects_unknown_venue(tmp_path):
    with pytest.raises(ValueError, match="unknown DBLP venues"):
        dblp_build.build(
            tmp_path / "index.parquet",
            snapshot="2026.09",
            selected_venues=["missing"],
            minimum_records=0,
        )


def test_install_downloads_and_validates_parquet(monkeypatch, tmp_path):
    source = tmp_path / "source.parquet"
    _create_fixture(source)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "installed"))

    result = dblp_index.install(snapshot=_snapshot("fixture", source))

    assert result["records"] == 1
    assert dblp_index.search("Diffusion Policy")[0]["doi"] == "10.15607/RSS.2023.XIX.026"
    assert dblp_index.status()["snapshot"] == "fixture"


def test_latest_snapshot_uses_newest_parquet_asset(monkeypatch):
    releases = [
        {
            "tag_name": "dblp-index-2026.06",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "dblp.parquet",
                    "id": 6,
                    "browser_download_url": "https://example.test/2026.06.parquet",
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        },
        {
            "tag_name": "dblp-index-2026.07.15",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "dblp.parquet",
                    "id": 7,
                    "browser_download_url": "https://example.test/2026.07.15.parquet",
                    "digest": "sha256:" + "b" * 64,
                }
            ],
        },
    ]

    monkeypatch.setattr(dblp_index, "_github_releases", lambda: releases)

    snapshot = dblp_index.latest_snapshot()

    assert snapshot.version == "2026.07.15"
    assert snapshot.sha256 == "b" * 64
    assert snapshot.asset_id == 7


def test_pinned_asset_resolution_uses_its_tag(monkeypatch):
    requested = []
    snapshot = dblp_index.Snapshot(
        "2020.01",
        f"https://github.com/{dblp_index.RELEASE_REPO}/releases/download/dblp-index-2020.01/dblp.parquet",
        "a" * 64,
    )

    def release(tag):
        requested.append(tag)
        return {"assets": [{"name": "dblp.parquet", "id": 42, "digest": "sha256:" + "a" * 64}]}

    monkeypatch.setattr(dblp_index, "_github_release", release)
    monkeypatch.setattr(
        dblp_index,
        "_github_releases",
        lambda: pytest.fail("pinned resolution must not scan the latest Releases page"),
    )

    assert dblp_index._private_asset_id(snapshot) == 42
    assert requested == ["dblp-index-2020.01"]


def test_install_fsyncs_asset_and_pointer_publication(monkeypatch, tmp_path):
    source = tmp_path / "source.parquet"
    _create_fixture(source)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "installed"))
    calls = []
    monkeypatch.setattr(dblp_index.os, "fsync", lambda descriptor: calls.append(descriptor))

    dblp_index.install(snapshot=_snapshot("fixture", source))

    assert len(calls) == (2 if dblp_index.os.name == "nt" else 4)


def test_update_skips_installed_latest_snapshot(monkeypatch, tmp_path):
    source = _install_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(dblp_index, "latest_snapshot", lambda: _snapshot("fixture", source))

    result = dblp_index.update()

    assert result["updated"] is False


def test_update_atomically_replaces_an_older_snapshot(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path, snapshot="2026.07")
    source = tmp_path / "new.parquet"
    _create_fixture(source, snapshot="2026.08")
    monkeypatch.setattr(dblp_index, "latest_snapshot", lambda: _snapshot("2026.08", source))

    result = dblp_index.update()

    assert result["updated"] is True
    assert dblp_index.status()["snapshot"] == "2026.08"


def test_failed_update_preserves_the_installed_index(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path)
    source = tmp_path / "invalid.parquet"
    source.write_bytes(b"not parquet")
    monkeypatch.setattr(
        dblp_index,
        "latest_snapshot",
        lambda: dblp_index.Snapshot("2026.08", source.resolve().as_uri(), "0" * 64, minimum_records=1),
    )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        dblp_index.update()

    assert dblp_index.status()["snapshot"] == "fixture"


def test_update_does_not_downgrade(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path, snapshot="2026.09")
    monkeypatch.setattr(
        dblp_index,
        "latest_snapshot",
        lambda: dblp_index.Snapshot("2026.08", "https://example.test/dblp.parquet", "a" * 64),
    )
    monkeypatch.setattr(
        dblp_index.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("unexpected download"),
    )

    result = dblp_index.update()

    assert result["updated"] is False
    assert result["snapshot"] == "2026.09"


def test_schema_failure_preserves_the_installed_index(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path)
    source = tmp_path / "wrong-schema.parquet"
    pl.DataFrame({"title": ["wrong"]}).write_parquet(
        source,
        metadata={
            "snapshot": "2026.08",
            "source": source.resolve().as_uri(),
            "records": "1",
            "minimum_records": "1",
            "coverage": "selected CS venues",
            "schema_version": dblp_index.SCHEMA_VERSION,
        },
    )
    monkeypatch.setattr(dblp_index, "latest_snapshot", lambda: _snapshot("2026.08", source))

    with pytest.raises(RuntimeError, match="schema mismatch"):
        dblp_index.update()

    assert dblp_index.status()["snapshot"] == "fixture"


def test_update_replaces_a_corrupt_installed_index(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path)
    corrupt = dblp_index.index_path()
    corrupt.write_bytes(b"not parquet")
    source = tmp_path / "valid.parquet"
    _create_fixture(source, snapshot="2026.08")
    monkeypatch.setattr(dblp_index, "latest_snapshot", lambda: _snapshot("2026.08", source))

    result = dblp_index.update()

    assert result["updated"] is True
    assert dblp_index.search("Diffusion Policy")


def test_corrupt_installed_index_raises_clean_runtime_error(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path)
    dblp_index.index_path().write_bytes(b"not parquet")

    with pytest.raises(RuntimeError, match="index is unreadable"):
        dblp_index.search("Diffusion Policy")


def test_wrong_schema_installed_index_raises_clean_runtime_error(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path)
    pl.DataFrame({"normalized_title": ["diffusionpolicy"]}).write_parquet(dblp_index.index_path())

    with pytest.raises(RuntimeError, match="schema mismatch"):
        dblp_index.search("Diffusion Policy")


def test_same_version_with_a_new_digest_is_installed(monkeypatch, tmp_path):
    _install_fixture(monkeypatch, tmp_path, snapshot="2026.08")
    source = tmp_path / "corrected.parquet"
    _create_fixture(source, snapshot="2026.08", title="Corrected Diffusion Policy")
    monkeypatch.setattr(dblp_index, "latest_snapshot", lambda: _snapshot("2026.08", source))

    result = dblp_index.update()

    assert result["updated"] is True
    assert dblp_index.status()["sha256"] == _digest(source)


def test_same_release_with_invalid_metadata_is_repaired(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "installed"))
    source = tmp_path / "release.parquet"
    _create_fixture(source, snapshot="2026.08")
    invalid = tmp_path / "invalid-metadata.parquet"
    _create_fixture(invalid, snapshot="2026.08", declared_records="2")
    invalid_snapshot = _snapshot("2026.08", invalid)
    dblp_index.data_dir().mkdir(parents=True)
    dblp_index._publish(invalid, invalid_snapshot, pl.read_parquet_metadata(invalid))
    monkeypatch.setattr(dblp_index, "latest_snapshot", lambda: _snapshot("2026.08", source))

    result = dblp_index.update()

    assert result["updated"] is True
    assert dblp_index.status()["records"] == 1


def test_partial_release_is_rejected(monkeypatch, tmp_path):
    source = tmp_path / "partial.parquet"
    _create_fixture(source, snapshot="2026.08")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "installed"))

    with pytest.raises(RuntimeError, match="expected at least"):
        dblp_index.install(
            snapshot=dblp_index.Snapshot("2026.08", source.resolve().as_uri(), _digest(source)),
        )


def test_install_removes_legacy_sqlite_files(monkeypatch, tmp_path):
    source = tmp_path / "source.parquet"
    _create_fixture(source)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "installed"))
    for legacy in dblp_index._legacy_paths():
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("legacy")

    dblp_index.install(snapshot=_snapshot("fixture", source))

    assert not any(path.exists() for path in dblp_index._legacy_paths())
