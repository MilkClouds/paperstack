import io
import json
import os
import stat
import sys

import pytest

from paperstack import cli, credentials, entrypoint


def _run(monkeypatch, *arguments, stdin=""):
    monkeypatch.setattr(sys, "argv", ["paperstack", *arguments])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return cli.main()


def test_store_is_private_and_environment_takes_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    credentials.set_value(credentials.SEMANTIC_SCHOLAR_API_KEY, "stored-key")

    path = credentials.credentials_path()
    assert credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY) == "stored-key"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert json.loads(path.read_text())["providers"]["semantic_scholar"]["api_key"] == "stored-key"

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "environment-key")
    assert credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY) == "environment-key"


def test_cli_set_status_and_unset_never_print_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    credentials.set_environment_context(set(os.environ), None)

    assert _run(monkeypatch, "config", "set", "semantic-scholar.api-key", "--stdin", stdin="secret-value\n") == 0
    assert _run(monkeypatch, "config", "status") == 0
    assert _run(monkeypatch, "config", "unset", "semantic-scholar.api-key") == 0

    output = capsys.readouterr()
    assert "secret-value" not in output.out
    assert "secret-value" not in output.err
    assert "credentials file" in output.out
    assert credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY) is None


def test_cli_interactive_set_uses_hidden_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "prompted-key")

    assert _run(monkeypatch, "config", "set", "semantic-scholar.api-key") == 0
    assert credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY) == "prompted-key"


def test_invalid_store_and_multiline_values_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    path = credentials.credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("not json")

    with pytest.raises(credentials.CredentialsError, match="unreadable"):
        credentials.get(credentials.SEMANTIC_SCHOLAR_API_KEY)

    path.unlink()
    with pytest.raises(credentials.CredentialsError, match="one nonempty line"):
        credentials.set_value(credentials.SEMANTIC_SCHOLAR_API_KEY, "two\nlines")


def test_dotenv_source_and_permissions_are_reported(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("SEMANTIC_SCHOLAR_API_KEY=dotenv-key\n")
    dotenv.chmod(0o644)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    entrypoint.load_environment()

    assert credentials.source(credentials.SEMANTIC_SCHOLAR_API_KEY) == "project .env"
    assert "chmod 600" in credentials.warnings()[0]
