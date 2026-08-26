import os

from paperstack import entrypoint


def test_load_environment_finds_nearest_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PAPERSTACK_TEST_VALUE=from-dotenv\n")
    child = tmp_path / "nested"
    child.mkdir()
    monkeypatch.chdir(child)
    monkeypatch.delenv("PAPERSTACK_TEST_VALUE", raising=False)

    entrypoint.load_environment()

    assert os.environ["PAPERSTACK_TEST_VALUE"] == "from-dotenv"


def test_load_environment_preserves_exported_value(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PAPERSTACK_TEST_VALUE=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPERSTACK_TEST_VALUE", "exported")

    entrypoint.load_environment()

    assert os.environ["PAPERSTACK_TEST_VALUE"] == "exported"


def test_load_environment_rejects_dotenv_native_library_paths(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("PDFIUM_LIB_PATH=untrusted.so\nORT_DYLIB_PATH=untrusted-ort.so\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PDFIUM_LIB_PATH", raising=False)
    monkeypatch.setenv("ORT_DYLIB_PATH", "exported-ort.so")

    entrypoint.load_environment()

    assert "PDFIUM_LIB_PATH" not in os.environ
    assert os.environ["ORT_DYLIB_PATH"] == "exported-ort.so"
