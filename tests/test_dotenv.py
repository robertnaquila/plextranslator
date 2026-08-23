import os

from plextranslator.config import load_dotenv


def _write(tmp_path, body):
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_loads_simple_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv("PT_TEST_KEY", raising=False)
    path = _write(tmp_path, "PT_TEST_KEY=abc123\n")
    applied = load_dotenv(path)
    assert applied == {"PT_TEST_KEY": "abc123"}
    assert os.environ["PT_TEST_KEY"] == "abc123"


def test_real_env_wins_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PT_TEST_KEY", "from-env")
    path = _write(tmp_path, "PT_TEST_KEY=from-file\n")
    load_dotenv(path)
    assert os.environ["PT_TEST_KEY"] == "from-env"


def test_override_true_replaces(tmp_path, monkeypatch):
    monkeypatch.setenv("PT_TEST_KEY", "from-env")
    path = _write(tmp_path, "PT_TEST_KEY=from-file\n")
    load_dotenv(path, override=True)
    assert os.environ["PT_TEST_KEY"] == "from-file"


def test_skips_comments_blanks_and_junk(tmp_path, monkeypatch):
    monkeypatch.delenv("PT_A", raising=False)
    monkeypatch.delenv("PT_B", raising=False)
    path = _write(tmp_path, "# a comment\n\nnot-a-pair\nPT_A=1\n  PT_B = 2 \n")
    applied = load_dotenv(path)
    assert applied == {"PT_A": "1", "PT_B": "2"}


def test_strips_quotes_and_export_prefix(tmp_path, monkeypatch):
    monkeypatch.delenv("PT_Q", raising=False)
    monkeypatch.delenv("PT_E", raising=False)
    path = _write(tmp_path, 'PT_Q="quoted value"\nexport PT_E=exported\n')
    applied = load_dotenv(path)
    assert applied["PT_Q"] == "quoted value"
    assert applied["PT_E"] == "exported"


def test_value_containing_equals_is_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("PT_URL", raising=False)
    path = _write(tmp_path, "PT_URL=http://x/y?a=b&c=d\n")
    assert load_dotenv(path)["PT_URL"] == "http://x/y?a=b&c=d"


def test_missing_file_is_not_an_error():
    assert load_dotenv("/nonexistent/.env") == {}
