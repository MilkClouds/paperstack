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


def test_current_converter_cache_does_not_require_the_extra(tmp_path, monkeypatch):
    paper_dir = tmp_path / "2601.00001"
    paper_dir.mkdir()
    (paper_dir / "paper.md").write_text("cached" * 20)
    (paper_dir / "meta.json").write_text(json.dumps({"converter": "pdf-inspector"}))
    monkeypatch.setattr(arxiv_pdf, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "pdf_inspector", None)

    assert arxiv_pdf.convert("2601.00001") is True
