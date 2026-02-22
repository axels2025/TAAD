"""Tests for KillSwitch — persistent trading halt mechanism."""

import json

import pytest

from src.services.kill_switch import KillSwitch


@pytest.fixture
def kill_switch(tmp_path):
    """Create a test-isolated KillSwitch."""
    halt_file = tmp_path / "kill_switch.json"
    return KillSwitch(halt_file=halt_file, register_signals=False)


class TestKillSwitch:
    """Test KillSwitch halt/resume/persistence."""

    def test_starts_not_halted(self, kill_switch):
        """New kill switch is not halted."""
        halted, reason = kill_switch.is_halted()
        assert halted is False
        assert reason == ""

    def test_halt(self, kill_switch):
        """halt() sets halted state with reason."""
        kill_switch.halt("Manual override")
        halted, reason = kill_switch.is_halted()
        assert halted is True
        assert reason == "Manual override"

    def test_resume(self, kill_switch):
        """resume() clears halted state."""
        kill_switch.halt("Test halt")
        kill_switch.resume()
        halted, reason = kill_switch.is_halted()
        assert halted is False
        assert reason == ""

    def test_halt_persists_to_file(self, tmp_path):
        """halt() writes state to file that a new instance can read."""
        halt_file = tmp_path / "persist_test.json"

        # First instance: halt
        ks1 = KillSwitch(halt_file=halt_file, register_signals=False)
        ks1.halt("Persistent halt test")

        # Verify file exists
        assert halt_file.exists()

        # Second instance: should load halted state
        ks2 = KillSwitch(halt_file=halt_file, register_signals=False)
        halted, reason = ks2.is_halted()
        assert halted is True
        assert reason == "Persistent halt test"

    def test_resume_removes_file(self, tmp_path):
        """resume() removes the halt file."""
        halt_file = tmp_path / "resume_test.json"

        ks = KillSwitch(halt_file=halt_file, register_signals=False)
        ks.halt("Will be resumed")
        assert halt_file.exists()

        ks.resume()
        assert not halt_file.exists()

    def test_no_file_means_not_halted(self, tmp_path):
        """No halt file on startup means trading is allowed."""
        halt_file = tmp_path / "nonexistent.json"
        ks = KillSwitch(halt_file=halt_file, register_signals=False)
        halted, reason = ks.is_halted()
        assert halted is False

    def test_corrupted_file_halts_for_safety(self, tmp_path):
        """Corrupted halt file → halt for safety."""
        halt_file = tmp_path / "corrupted.json"
        halt_file.write_text("not valid json {{{")

        ks = KillSwitch(halt_file=halt_file, register_signals=False)
        halted, reason = ks.is_halted()
        assert halted is True
        assert "Corrupted" in reason

    def test_get_status(self, kill_switch):
        """get_status() returns full status dict."""
        kill_switch.halt("Status test")
        status = kill_switch.get_status()
        assert status["halted"] is True
        assert status["reason"] == "Status test"
        assert status["halt_time"] is not None

    def test_get_status_not_halted(self, kill_switch):
        """get_status() when not halted."""
        status = kill_switch.get_status()
        assert status["halted"] is False
        assert status["reason"] == ""
        assert status["halt_time"] is None

    def test_file_content_is_valid_json(self, tmp_path):
        """Halt file is valid JSON with expected keys."""
        halt_file = tmp_path / "json_test.json"
        ks = KillSwitch(halt_file=halt_file, register_signals=False)
        ks.halt("JSON test")

        with open(halt_file) as f:
            data = json.load(f)

        assert data["halted"] is True
        assert data["reason"] == "JSON test"
        assert "halt_time" in data
