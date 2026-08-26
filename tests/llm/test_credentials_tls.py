"""Tests for pasted-credential cleanup and system trust store setup."""

from __future__ import annotations

from iris.app.core import tls
from iris.app.llm.cloud import build_provider, clean_credential


class TestCleanCredential:
    def test_none_passthrough(self):
        assert clean_credential(None) is None

    def test_plain_key_untouched(self):
        assert clean_credential("gsk_abc123") == "gsk_abc123"

    def test_strips_whitespace_and_newlines(self):
        assert clean_credential("  gsk_abc123\r\n") == "gsk_abc123"

    def test_strips_wrapping_double_quotes(self):
        assert clean_credential('"gsk_abc123"') == "gsk_abc123"

    def test_strips_wrapping_single_quotes(self):
        assert clean_credential("'gsk_abc123'") == "gsk_abc123"

    def test_strips_quotes_then_whitespace(self):
        assert clean_credential(' "  gsk_abc123 " ') == "gsk_abc123"

    def test_inner_quotes_kept(self):
        assert clean_credential('ab"cd') == 'ab"cd'

    def test_empty_becomes_none(self):
        assert clean_credential("  ") is None
        assert clean_credential('""') is None


class TestBuildProviderSanitizes:
    def test_key_and_url_are_cleaned(self):
        provider = build_provider(
            "groq",
            {"api_key": '"gsk_x"\n', "base_url": " https://api.groq.com/openai/v1 ", "model": " llama-3.3-70b-versatile "},
        )
        assert provider.api_key == "gsk_x"
        assert provider.base_url == "https://api.groq.com/openai/v1"
        assert provider.default_model == "llama-3.3-70b-versatile"
        assert provider.configured

    def test_whitespace_only_key_means_unconfigured(self):
        provider = build_provider("groq", {"api_key": "   ", "base_url": "https://x", "model": "m"})
        assert provider.api_key is None
        assert not provider.configured


class TestSystemTrustStore:
    def test_returns_bool_and_never_raises(self, monkeypatch):
        monkeypatch.setattr(tls, "_injected", False)
        result = tls.use_system_trust_store()
        assert isinstance(result, bool)

    def test_idempotent_after_success(self, monkeypatch):
        monkeypatch.setattr(tls, "_injected", True)
        assert tls.use_system_trust_store() is True

    def test_missing_truststore_is_nonfatal(self, monkeypatch):
        import builtins

        monkeypatch.setattr(tls, "_injected", False)
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "truststore":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert tls.use_system_trust_store() is False
