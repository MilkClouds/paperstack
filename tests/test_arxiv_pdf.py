import hashlib
import json
import sys
from types import SimpleNamespace

from paperstack.content import arxiv_pdf


def _ocr_result(markdown: str = "x" * 200):
    timings = SimpleNamespace(render_ms=10, ocr_ms=20, assembly_ms=2)
    model = SimpleNamespace(name="PP-OCRv6 Small", revision="test")
    provenance = SimpleNamespace(
        source="ocr",
        ocr_model=model,
        render_dpi=150.0,
        ocr_confidence=0.95,
        hosted_recommended=False,
        warnings=[],
        timings=timings,
    )
    return SimpleNamespace(
        markdown=markdown,
        page_count=1,
        pages_recommended_for_ocr=[1],
        pages_routed_to_ocr=[1],
        pages_recommending_hosted=[],
        ocr_reasons_by_page=[SimpleNamespace(page=1, reasons=["scanned"])],
        pages_with_tables=[],
        pages_with_columns=[],
        pages=[SimpleNamespace(page_number=1, provenance=provenance)],
    )


def _native_result(markdown: str | None = "n" * 200):
    result = _ocr_result(markdown)
    result.pages_routed_to_ocr = []
    result.pages[0].provenance.source = "native"
    result.pages[0].provenance.ocr_model = None
    result.pages[0].provenance.ocr_confidence = None
    return result


def test_missing_converter_names_the_installable_extra(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "pdf_inspector", None)

    assert arxiv_pdf.convert("2601.00001") is False
    error = capsys.readouterr().err
    assert "paperstack-cli[pdf]" in error
    assert "uv tool install --force" in error


def test_selective_ocr_writes_page_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", lambda _url: b"%PDF-test")
    monkeypatch.setattr(arxiv_pdf.importlib.metadata, "version", lambda _name: "1.17.0")
    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr=lambda _path, mode: _ocr_result()),
    )

    assert arxiv_pdf.convert("2601.00001") is True
    meta = json.loads((tmp_path / "2601.00001" / "meta.json").read_text())
    assert meta["converter"] == "pdf-inspector"
    assert meta["converter_version"] == "1.17.0"
    assert meta["conversion_mode"] == "selective_ocr"
    assert meta["pages_routed_to_ocr"] == [1]
    assert meta["pages"][0]["source"] == "ocr"
    assert meta["pages"][0]["ocr_model"]["revision"] == "test"


def test_selective_ocr_writes_the_hashed_markdown_bytes(tmp_path, monkeypatch):
    markdown = ("첫 줄\nsecond line\n" * 10).strip()
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", lambda _url: b"%PDF-test")
    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr=lambda _path, mode: _ocr_result(markdown)),
    )

    assert arxiv_pdf.convert("2601.00001") is True
    paper_dir = tmp_path / "2601.00001"
    assert (paper_dir / "paper.md").read_bytes() == markdown.encode("utf-8")
    assert arxiv_pdf._cached_conversion(paper_dir) == paper_dir / "paper.md"


def test_runtime_failure_caches_partial_native_extraction(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", lambda _url: b"%PDF-test")

    def convert_pdf(_path, mode):
        if mode == "auto":
            raise ValueError("failed to load PDFium")
        return _native_result()

    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr=convert_pdf),
    )

    assert arxiv_pdf.convert("2601.00001") is True
    meta = json.loads((tmp_path / "2601.00001" / "meta.json").read_text())
    assert meta["conversion_mode"] == "native_fallback"
    assert meta["quality"] == "partial"
    assert meta["ocr_error"] == "failed to load PDFium"
    assert "cached partial native extraction" in capsys.readouterr().err


def test_runtime_failure_rejects_a_fully_scanned_pdf(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", lambda _url: b"%PDF-test")

    def convert_pdf(_path, mode):
        if mode == "auto":
            raise ValueError("failed to load PDFium")
        return _native_result(None)

    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr=convert_pdf),
    )

    assert arxiv_pdf.convert("2601.00001") is False
    error = capsys.readouterr().err
    assert "failed to load PDFium" in error
    assert "PDFIUM_LIB_PATH" in error
    assert "ORT_DYLIB_PATH" in error
    assert arxiv_pdf.OCR_RUNTIME_GUIDE in error


def test_legacy_converter_cache_is_rebuilt(tmp_path, monkeypatch):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    (paper_dir / "paper.pdf").write_bytes(b"%PDF-test")
    (paper_dir / "paper.md").write_text("old" * 50)
    (paper_dir / "meta.json").write_text(json.dumps({"converter": "pymupdf4llm"}))
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr=lambda _path, mode: _ocr_result("new" * 50)),
    )

    assert arxiv_pdf.convert("2601.00001") is True
    assert (paper_dir / "paper.md").read_text() == "new" * 50
    assert json.loads((paper_dir / "meta.json").read_text())["converter"] == "pdf-inspector"


def test_corrupt_cached_pdf_is_refetched_once(tmp_path, monkeypatch):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    pdf_path = paper_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-broken")
    fetches = []

    def fetch(url):
        fetches.append(url)
        return b"%PDF-repaired"

    def convert_pdf(path, mode):
        if pdf_path.read_bytes() == b"%PDF-broken":
            raise ValueError("malformed PDF")
        return _ocr_result("repaired" * 25)

    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", fetch)
    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr=convert_pdf),
    )

    assert arxiv_pdf.convert("2601.00001") is True
    assert fetches == ["https://arxiv.org/pdf/2601.00001"]
    assert pdf_path.read_bytes() == b"%PDF-repaired"
    assert (paper_dir / "paper.md").read_text() == "repaired" * 25


def test_unusable_cached_pdf_output_is_refetched_once(tmp_path, monkeypatch):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    pdf_path = paper_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-truncated")
    fetches = []

    def fetch(url):
        fetches.append(url)
        return b"%PDF-repaired"

    def convert_pdf(path, mode):
        markdown = "short" if pdf_path.read_bytes() == b"%PDF-truncated" else "repaired" * 25
        return _ocr_result(markdown)

    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(arxiv_pdf, "_fetch", fetch)
    monkeypatch.setitem(
        sys.modules,
        "pdf_inspector",
        SimpleNamespace(process_pdf_with_ocr=convert_pdf),
    )

    assert arxiv_pdf.convert("2601.00001") is True
    assert fetches == ["https://arxiv.org/pdf/2601.00001"]
    assert pdf_path.read_bytes() == b"%PDF-repaired"
    assert (paper_dir / "paper.md").read_text() == "repaired" * 25


def test_current_converter_cache_does_not_require_the_extra(tmp_path, monkeypatch):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    markdown = "cached" * 20
    encoded = markdown.encode()
    (paper_dir / "paper.md").write_bytes(encoded)
    (paper_dir / "meta.json").write_text(
        json.dumps(
            {
                "converter": "pdf-inspector",
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    )
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "pdf_inspector", None)

    assert arxiv_pdf.convert("2601.00001") is True


def test_current_converter_cache_rejects_mismatched_markdown(tmp_path):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    original = b"original" * 20
    (paper_dir / "paper.md").write_bytes(b"corrupted" * 20)
    (paper_dir / "meta.json").write_text(
        json.dumps(
            {
                "converter": "pdf-inspector",
                "bytes": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
            }
        )
    )

    assert arxiv_pdf._cached_conversion(paper_dir) is None


def test_current_converter_cache_rejects_non_object_metadata(tmp_path):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    (paper_dir / "paper.md").write_text("cached" * 20)
    (paper_dir / "meta.json").write_text("null")

    assert arxiv_pdf._cached_conversion(paper_dir) is None
