import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperstack.content import arxiv_source


def _tar(files: dict[str, bytes]) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return data.getvalue()


class SourceTest(unittest.TestCase):
    def test_load_document_includes_text_before_first_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            source = cache / "test" / "src"
            source.mkdir(parents=True)
            (source / "main.tex").write_text(
                r"\documentclass{article}\begin{document}Abstract text."
                r"\section{Method}Body.\end{document}"
            )
            with patch.object(arxiv_source, "CACHE_DIR", cache):
                body, sections = arxiv_source._load_document("test", refresh=False)
            self.assertIn("Abstract text.", body)
            self.assertIn(r"\section{Method}Body.", body)
            self.assertEqual(sections[0]["title"], "Method")

    def test_expands_subfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "main.tex").write_text(r"\subfile{part}")
            (source / "part.tex").write_text(r"\section{Included}")
            original = arxiv_source.shutil.which
            with patch.object(
                arxiv_source.shutil,
                "which",
                side_effect=lambda name: None if name == "latexpand" else original(name),
            ):
                flattened = arxiv_source._flatten(source / "main.tex", source)
            self.assertIn(r"\section{Included}", flattened)

    def test_refresh_replaces_conflicting_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            source = cache / "test" / "src"
            (source / "collision").mkdir(parents=True)
            (source / "collision" / "old.tex").write_text("old")
            raw = _tar({"main.tex": b"new", "collision": b"file"})
            with (
                patch.object(arxiv_source, "CACHE_DIR", cache),
                patch.object(arxiv_source, "_fetch_bytes", return_value=raw),
            ):
                refreshed = arxiv_source._ensure_source("test", refresh=True)
            self.assertEqual((refreshed / "collision").read_text(), "file")

    def test_failed_refresh_preserves_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            source = cache / "test" / "src"
            source.mkdir(parents=True)
            (source / "main.tex").write_text("old")
            with (
                patch.object(arxiv_source, "CACHE_DIR", cache),
                patch.object(arxiv_source, "_fetch_bytes", return_value=b"%PDF-broken"),
                self.assertRaises(SystemExit),
            ):
                arxiv_source._ensure_source("test", refresh=True)
            self.assertEqual((source / "main.tex").read_text(), "old")


if __name__ == "__main__":
    unittest.main()
