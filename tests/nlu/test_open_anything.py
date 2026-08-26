"""Tests for natural-language file/folder opening (find_and_open routing)."""

from __future__ import annotations

import pytest

from iris.app.nlu.engine import IntentEngine


class TestOpenAnything:
    engine = IntentEngine()

    @pytest.mark.parametrize("utterance,tool,args_subset", [
        ("open report.pdf", "find_and_open", {"name": "report"}),
        ("open downloads/report.pdf", "open_path", {"path": "downloads/report.pdf"}),
        ("open ~/downloads", "open_path", {"path": "~/downloads"}),
        ("open my latest screenshot", "find_and_open", {"kind": "screenshot", "latest": True}),
        ("open the last ppt", "find_and_open", {"kind": "ppt", "latest": True}),
        ("open that pdf i made yesterday", "find_and_open", {"kind": "pdf", "latest": True}),
        ("open the file budget 2026", "find_and_open", {"name": "budget 2026"}),
        ("show me my photos", "open_path", {"path": "~/Pictures"}),
        ("show my downloads folder", "open_path", {"path": "~/Downloads"}),
        ("open my pictures folder", "open_path", {"path": "~/Pictures"}),
        ("open the music folder", "open_path", {"path": "~/Music"}),
        ("open home folder", "open_path", {"path": "~"}),
    ])
    def test_open_phrases_route(self, utterance, tool, args_subset):
        match = self.engine.match(utterance)
        assert match is not None, f"no match for {utterance!r}"
        assert match.tool_name == tool
        for key, value in args_subset.items():
            assert match.arguments.get(key) == value

    def test_websites_and_apps_unaffected(self):
        assert self.engine.match("open youtube").tool_name == "open_website"
        assert self.engine.match("open notepad").tool_name == "open_app"
        assert self.engine.match("open example.com").tool_name == "open_website"

    def test_show_files_still_lists(self):
        match = self.engine.match("show my files")
        assert match and match.tool_name == "list_directory"
