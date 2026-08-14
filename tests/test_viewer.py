import json

import pytest

from paperstack import entry_types, viewer


def _corpus(tmp_path):
    entry = tmp_path / "entries" / "papers" / "example2026paper.md"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        entry_types.render_scaffold(
            "paper",
            entry_id="arxiv:2601.00001",
            title="An Example Paper",
            editor="tester",
        )
    )
    (tmp_path / "entries" / "talks").mkdir()
    (tmp_path / "entries" / "posts").mkdir()
    (tmp_path / "entries" / "collections.json").write_text(
        json.dumps(
            {
                "version": 1,
                "collections": [
                    {
                        "id": "examples",
                        "status": "published",
                        "title": "Examples",
                        "description": "Synthetic entries",
                        "entries": ["example2026paper"],
                    }
                ],
            }
        )
    )
    (tmp_path / "entries" / "citations.json").write_text(json.dumps({"last_updated": None, "papers": {}}))
    return tmp_path


def test_build_writes_self_contained_static_viewer(tmp_path):
    root = _corpus(tmp_path / "corpus")
    output = tmp_path / "site"

    count = viewer.build(root, output)

    assert count == 1
    assert (output / "index.html").is_file()
    assert (output / "assets" / "app.js").is_file()
    assert (output / "assets" / "marked.min.js").is_file()
    assert (output / "entries" / "papers" / "example2026paper.md").is_file()
    data = json.loads((output / "data.json").read_text())
    assert data["entries"][0]["key"] == "example2026paper"
    assert data["collections"][0]["id"] == "examples"


def test_build_rejects_missing_collection_member(tmp_path):
    root = _corpus(tmp_path / "corpus")
    path = root / "entries" / "collections.json"
    document = json.loads(path.read_text())
    document["collections"][0]["entries"] = ["missing"]
    path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="missing entries"):
        viewer.build(root, tmp_path / "site")


def test_build_serializes_yaml_dates(tmp_path):
    root = _corpus(tmp_path / "corpus")
    post = root / "entries" / "posts" / "example2026post.md"
    post.write_text(
        entry_types.render_scaffold(
            "post",
            entry_id="https://example.test/post",
            title="An Example Post",
            editor="tester",
            publisher="Example",
            published="2026-01-01",
        )
    )
    collections = root / "entries" / "collections.json"
    document = json.loads(collections.read_text())
    document["collections"][0]["entries"].append("example2026post")
    collections.write_text(json.dumps(document))

    viewer.build(root, tmp_path / "site")

    data = json.loads((tmp_path / "site" / "data.json").read_text())
    assert next(entry for entry in data["entries"] if entry["kind"] == "post")["published"] == "2026-01-01"


def test_build_refuses_to_replace_unrelated_directory(tmp_path):
    root = _corpus(tmp_path / "corpus")
    output = tmp_path / "unrelated"
    output.mkdir()
    (output / "keep.txt").write_text("precious")

    with pytest.raises(ValueError, match="not created by Paperstack"):
        viewer.build(root, output)

    assert (output / "keep.txt").read_text() == "precious"


def test_build_refuses_output_inside_authored_entries(tmp_path):
    root = _corpus(tmp_path / "corpus")

    with pytest.raises(ValueError, match="authored entries"):
        viewer.build(root, root / "entries" / "generated")
