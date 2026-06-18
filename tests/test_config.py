import subprocess
import sys

from post_to_album.config import load_config


def test_package_imports_from_editable_install_outside_repo(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", "import post_to_album; print(post_to_album.__all__)"] ,
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_load_config_rejects_empty_wordpress_base_url(monkeypatch):
    monkeypatch.setattr("post_to_album.config.load_dotenv", lambda: None)
    monkeypatch.setenv("WORDPRESS_BASE_URL", "")

    try:
        load_config([])
    except ValueError as exc:
        assert "WORDPRESS_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_config_rejects_missing_wordpress_base_url(monkeypatch):
    monkeypatch.setattr("post_to_album.config.load_dotenv", lambda: None)
    monkeypatch.delenv("WORDPRESS_BASE_URL", raising=False)

    try:
        load_config([])
    except ValueError as exc:
        assert "WORDPRESS_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_config_defaults_to_dry_run(monkeypatch):
    monkeypatch.setattr("post_to_album.config.load_dotenv", lambda: None)
    monkeypatch.setenv("WORDPRESS_BASE_URL", "https://example.com")
    monkeypatch.setenv("LASTFM_API_KEY", "key")

    cfg = load_config([])

    assert cfg.apply is False
    assert cfg.batch_size == 20
    assert cfg.wordpress_base_url == "https://example.com"
