"""A list setting written the obvious way must not stop IRIS booting.

The setup dialog saves the provider order as ``LLM_PROVIDER_ORDER=groq,gemini``
— which is also what anyone editing ``.env`` by hand would write. pydantic
JSON-decodes any field typed as a list *before* a validator can see it, so that
line did not merely parse oddly: it raised at import time, and IRIS refused to
start with a JSONDecodeError pointing into pydantic rather than at the file.

The failure landed on someone who had just successfully connected their key,
which is the worst possible moment for it.
"""

from __future__ import annotations

import pytest

from iris.app.core.config import Settings


def _settings_from(tmp_path, body: str) -> Settings:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return Settings(_env_file=str(env))


class TestListSettingsFromEnv:
    def test_plain_csv_is_what_the_setup_dialog_writes(self, tmp_path):
        s = _settings_from(tmp_path, "LLM_PROVIDER_ORDER=groq,gemini,openrouter\n")
        assert s.LLM_PROVIDER_ORDER == ["groq", "gemini", "openrouter"]

    def test_json_still_works(self, tmp_path):
        s = _settings_from(tmp_path, 'LLM_PROVIDER_ORDER=["groq","gemini"]\n')
        assert s.LLM_PROVIDER_ORDER == ["groq", "gemini"]

    def test_a_single_value_is_a_list_of_one(self, tmp_path):
        assert _settings_from(tmp_path, "LLM_PROVIDER_ORDER=groq\n").LLM_PROVIDER_ORDER == ["groq"]

    def test_spaces_around_the_commas_are_forgiven(self, tmp_path):
        s = _settings_from(tmp_path, "LLM_PROVIDER_ORDER= groq , gemini \n")
        assert s.LLM_PROVIDER_ORDER == ["groq", "gemini"]

    @pytest.mark.parametrize("field,line,expected", [
        ("WAKE_WORDS", "WAKE_WORDS=iris,hey iris,ok iris", ["iris", "hey iris", "ok iris"]),
        ("CORS_ORIGINS", "CORS_ORIGINS=http://a.test, http://b.test",
         ["http://a.test", "http://b.test"]),
        ("NEWS_FEEDS", "NEWS_FEEDS=https://a.test/rss", ["https://a.test/rss"]),
        ("FS_ALLOWED_ROOTS", "FS_ALLOWED_ROOTS=/tmp/a,/tmp/b", ["/tmp/a", "/tmp/b"]),
        ("TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_ALLOWED_USER_IDS=1,2,3", ["1", "2", "3"]),
    ])
    def test_every_list_setting_takes_csv(self, tmp_path, field, line, expected):
        """Not just the one that broke — the same trap was set for all of them."""
        assert getattr(_settings_from(tmp_path, line + "\n"), field) == expected

    def test_a_file_the_setup_dialog_actually_produced_boots(self, tmp_path):
        """The exact shape of a .env after connecting a key, start to finish."""
        s = _settings_from(tmp_path, "\n".join([
            "# IRIS configuration",
            "GEMINI_API_KEY=AIzaTESTKEYNOTREAL",
            "GEMINI_MODEL=gemini-flash-latest",
            "VISION_MODEL=gemini-flash-latest",
            "LLM_PROVIDER_ORDER=gemini,groq,openrouter",
            "USER_NAME=Prajjwal",
            "",
        ]))
        assert s.LLM_PROVIDER_ORDER == ["gemini", "groq", "openrouter"]
        assert s.USER_NAME == "Prajjwal"
        assert s.GEMINI_MODEL == "gemini-flash-latest"
