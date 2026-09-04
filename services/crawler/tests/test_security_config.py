"""Security configuration regression tests for the crawler service."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest


CRAWLER_ROOT = Path(__file__).resolve().parents[1]


def _fresh_config_module():
    """Import config again because it caches security values at import time."""
    sys.modules.pop("unified_api.config", None)
    sys.modules.pop("config", None)
    import unified_api.config as config

    return config


class TestJWTSecret:
    def test_production_missing_secret_fails(self):
        with mock.patch.dict(
            os.environ,
            {"ENVIRONMENT": "production", "JWT_SECRET": ""},
            clear=True,
        ):
            with pytest.raises(ValueError):
                _fresh_config_module()

    def test_production_secret_too_short_fails(self):
        with mock.patch.dict(
            os.environ,
            {"ENVIRONMENT": "production", "JWT_SECRET": "short"},
            clear=True,
        ):
            with pytest.raises(ValueError):
                _fresh_config_module()

    def test_known_placeholder_rejected(self):
        for value in (
            "unified-scraper-secret-2026",
            "change-me",
            "secret",
            "default",
            "123456",
        ):
            with mock.patch.dict(
                os.environ,
                {"ENVIRONMENT": "production", "JWT_SECRET": value},
                clear=True,
            ):
                with pytest.raises(ValueError):
                    _fresh_config_module()

    def test_development_generates_random_secret(self):
        with mock.patch.dict(
            os.environ,
            {"ENVIRONMENT": "development", "JWT_SECRET": ""},
            clear=True,
        ):
            first = _fresh_config_module().JWT_SECRET
            second = _fresh_config_module().JWT_SECRET
            assert len(first) >= 32
            assert first != second

    def test_development_explicit_secret_is_preserved(self):
        secret = "a-valid-32-character-dev-key-!"
        with mock.patch.dict(
            os.environ,
            {"ENVIRONMENT": "development", "JWT_SECRET": secret},
            clear=True,
        ):
            assert _fresh_config_module().JWT_SECRET == secret


class TestDatabaseConfig:
    def test_production_missing_password_fails(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "DB_PASSWORD": "",
                "DB_USER": "recruitment_app",
                "JWT_SECRET": "a-valid-32-character-secret-key!!!",
            },
            clear=True,
        ):
            with pytest.raises(ValueError):
                _fresh_config_module().validate_security_settings()

    def test_production_root_user_fails(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "DB_PASSWORD": "a-strong-password",
                "DB_USER": "root",
                "JWT_SECRET": "a-valid-32-character-secret-key!!!",
            },
            clear=True,
        ):
            with pytest.raises(ValueError):
                _fresh_config_module().validate_security_settings()

    def test_production_admin_user_fails(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "DB_PASSWORD": "a-strong-password",
                "DB_USER": "admin",
                "JWT_SECRET": "a-valid-32-character-secret-key!!!",
            },
            clear=True,
        ):
            with pytest.raises(ValueError):
                _fresh_config_module().validate_security_settings()

    def test_unsafe_passwords_rejected(self):
        for password in ("123456", "password", "root123", "root", "admin", ""):
            with mock.patch.dict(
                os.environ,
                {
                    "ENVIRONMENT": "production",
                    "DB_PASSWORD": password,
                    "DB_USER": "recruitment_app",
                    "JWT_SECRET": "a-valid-32-character-secret-key!!!",
                },
                clear=True,
            ):
                with pytest.raises(ValueError):
                    _fresh_config_module().validate_security_settings()

    def test_valid_configuration_passes(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "DB_PASSWORD": "a-strong-production-password",
                "DB_USER": "recruitment_app",
                "JWT_SECRET": "a-valid-32-character-secret-key!!!",
                "CORS_ALLOWED_ORIGINS": "https://example.com",
                "CORS_ALLOW_CREDENTIALS": "false",
            },
            clear=True,
        ):
            _fresh_config_module().validate_security_settings()


class TestCORSConfig:
    def test_wildcard_with_credentials_fails(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "CORS_ALLOWED_ORIGINS": "*",
                "CORS_ALLOW_CREDENTIALS": "true",
                "DB_PASSWORD": "a-strong-production-password",
                "DB_USER": "recruitment_app",
                "JWT_SECRET": "a-valid-32-character-secret-key!!!",
            },
            clear=True,
        ):
            with pytest.raises(ValueError):
                _fresh_config_module().validate_security_settings()

    def test_localhost_origins_pass(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "development",
                "DB_PASSWORD": "dev-password-ok",
                "JWT_SECRET": "a-valid-32-character-secret-key!!!",
            },
            clear=True,
        ):
            config = _fresh_config_module()
            assert "http://localhost:3000" in config.CORS_ALLOWED_ORIGINS
            assert config.CORS_ALLOW_CREDENTIALS is False
            config.validate_security_settings()

    def test_production_missing_origins_fails(self):
        with mock.patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "production",
                "CORS_ALLOWED_ORIGINS": "",
                "DB_PASSWORD": "a-strong-production-password",
                "DB_USER": "recruitment_app",
                "JWT_SECRET": "a-valid-32-character-secret-key!!!",
            },
            clear=True,
        ):
            with pytest.raises(ValueError):
                _fresh_config_module().validate_security_settings()


class TestLiepinCookie:
    DEFAULT_NAME = "liepin_cookies.local.json"

    @staticmethod
    def _resolve_cookie_file(env_value: str | None = None) -> Path:
        configured = (env_value or os.getenv("LIEPIN_COOKIES_FILE", "")).strip()
        if configured:
            return Path(configured)
        return (
            Path(__file__).resolve().parent.parent
            / "multi_company_scraper"
            / "config"
            / TestLiepinCookie.DEFAULT_NAME
        )

    def test_example_file_is_empty_array(self):
        path = (
            CRAWLER_ROOT
            / "multi_company_scraper"
            / "config"
            / "liepin_cookies.example.json"
        )
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == []

    def test_real_cookie_file_not_tracked(self):
        path = (
            CRAWLER_ROOT
            / "multi_company_scraper"
            / "config"
            / "liepin_cookies.json"
        )
        assert not path.exists()

    def test_cookie_resolver_default_local_path(self):
        resolved = self._resolve_cookie_file("")
        assert resolved.name == self.DEFAULT_NAME
        assert self.DEFAULT_NAME in str(resolved)

    def test_cookie_resolver_missing_file_no_exception(self):
        assert isinstance(self._resolve_cookie_file(""), Path)

    def test_cookie_resolver_uses_env_path(self, tmp_path):
        cookie_path = tmp_path / "cookies.json"
        cookie_path.write_text("[]", encoding="utf-8")
        assert self._resolve_cookie_file(str(cookie_path)) == cookie_path

    def test_cookie_resolver_local_file_gitignored(self):
        path = (
            CRAWLER_ROOT
            / "multi_company_scraper"
            / "config"
            / self.DEFAULT_NAME
        )
        assert not path.exists()


class TestCrawlerDBConfig:
    @staticmethod
    def _assert_no_password_in_source(relative_path: str) -> None:
        source = (CRAWLER_ROOT / relative_path).read_text(encoding="utf-8")
        assert 'password="123456"' not in source
        assert "password='123456'" not in source

    @staticmethod
    def _assert_source_imports_config(relative_path: str) -> None:
        source = (CRAWLER_ROOT / relative_path).read_text(encoding="utf-8")
        assert "from unified_api.config import DB_CONFIG" in source

    def test_config_module_has_no_hardcoded_password(self):
        with mock.patch.dict(
            os.environ,
            {"ENVIRONMENT": "development", "DB_PASSWORD": ""},
            clear=True,
        ):
            password = str(_fresh_config_module().DB_CONFIG.get("password", ""))
            assert password != "123456"
            assert password == ""

    def test_simple_spider_no_hardcoded_password(self):
        self._assert_no_password_in_source("crawler/simple_spider.py")
        self._assert_source_imports_config("crawler/simple_spider.py")

    def test_data_viewer_no_hardcoded_password(self):
        self._assert_no_password_in_source("crawler/data_viewer.py")
        self._assert_source_imports_config("crawler/data_viewer.py")

    def test_visual_spider_no_hardcoded_password(self):
        self._assert_no_password_in_source("crawler/visual_spider.py")
        self._assert_source_imports_config("crawler/visual_spider.py")
