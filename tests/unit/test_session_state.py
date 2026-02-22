"""Unit tests for TradeSessionState class.

Tests session state persistence, recovery, and serialization.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.execution.session_state import SessionState, TradeSessionState


@pytest.fixture
def temp_session_dir(tmp_path):
    """Create temporary session directory for testing."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


@pytest.fixture
def sample_opportunities():
    """Create sample opportunity data for testing."""
    return [
        {
            "symbol": "AAPL",
            "strike": 150.0,
            "expiration": "2024-03-15",
            "premium": 0.50,
            "contracts": 5,
        },
        {
            "symbol": "MSFT",
            "strike": 300.0,
            "expiration": "2024-03-15",
            "premium": 0.75,
            "contracts": 5,
        },
        {
            "symbol": "GOOGL",
            "strike": 120.0,
            "expiration": "2024-03-15",
            "premium": 0.60,
            "contracts": 5,
        },
    ]


@pytest.fixture
def sample_session_state(sample_opportunities):
    """Create sample SessionState for testing."""
    return SessionState(
        session_id="20240115_123456",
        timestamp=datetime(2024, 1, 15, 12, 34, 56),
        phase="execution",
        opportunities=sample_opportunities,
        approved=[0, 1, 2],
        executed=[0, 1],
        failed=[],
        metadata={"max_positions": 10, "risk_limit": 5000.0},
    )


class TestSessionStateDataclass:
    """Test SessionState dataclass."""

    def test_session_state_initialization(self, sample_opportunities):
        """Test SessionState initializes with correct attributes."""
        # Arrange & Act
        state = SessionState(
            session_id="test_session",
            timestamp=datetime(2024, 1, 15, 12, 0, 0),
            phase="approval",
            opportunities=sample_opportunities,
            approved=[0, 1],
            executed=[],
            failed=[],
            metadata={"test": "value"},
        )

        # Assert
        assert state.session_id == "test_session"
        assert state.timestamp == datetime(2024, 1, 15, 12, 0, 0)
        assert state.phase == "approval"
        assert len(state.opportunities) == 3
        assert state.approved == [0, 1]
        assert state.executed == []
        assert state.failed == []
        assert state.metadata == {"test": "value"}

    def test_session_state_to_dict(self, sample_session_state):
        """Test SessionState.to_dict() serialization."""
        # Act
        result = sample_session_state.to_dict()

        # Assert
        assert isinstance(result, dict)
        assert result["session_id"] == "20240115_123456"
        assert result["timestamp"] == "2024-01-15T12:34:56"
        assert result["phase"] == "execution"
        assert result["opportunities"] == sample_session_state.opportunities
        assert result["approved"] == [0, 1, 2]
        assert result["executed"] == [0, 1]
        assert result["failed"] == []
        assert result["metadata"] == {"max_positions": 10, "risk_limit": 5000.0}

    def test_session_state_from_dict(self, sample_session_state):
        """Test SessionState.from_dict() deserialization."""
        # Arrange
        data = sample_session_state.to_dict()

        # Act
        restored = SessionState.from_dict(data)

        # Assert
        assert restored.session_id == sample_session_state.session_id
        assert restored.timestamp == sample_session_state.timestamp
        assert restored.phase == sample_session_state.phase
        assert restored.opportunities == sample_session_state.opportunities
        assert restored.approved == sample_session_state.approved
        assert restored.executed == sample_session_state.executed
        assert restored.failed == sample_session_state.failed
        assert restored.metadata == sample_session_state.metadata

    def test_session_state_round_trip(self, sample_session_state):
        """Test to_dict() and from_dict() round-trip conversion."""
        # Act
        data = sample_session_state.to_dict()
        restored = SessionState.from_dict(data)

        # Assert
        assert restored.session_id == sample_session_state.session_id
        assert restored.timestamp == sample_session_state.timestamp
        assert restored.phase == sample_session_state.phase
        assert restored.approved == sample_session_state.approved
        assert restored.executed == sample_session_state.executed
        assert restored.failed == sample_session_state.failed

    def test_session_state_from_dict_missing_metadata(self, sample_session_state):
        """Test from_dict handles missing metadata gracefully."""
        # Arrange
        data = sample_session_state.to_dict()
        del data["metadata"]

        # Act
        restored = SessionState.from_dict(data)

        # Assert
        assert restored.metadata == {}


class TestTradeSessionStateInitialization:
    """Test TradeSessionState initialization."""

    def test_initialization_creates_session_id(self, temp_session_dir, monkeypatch):
        """Test initialization creates unique session ID."""
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)

        session = TradeSessionState()

        assert session.session_id is not None
        assert len(session.session_id) > 0
        assert "_" in session.session_id  # Format: YYYYMMDD_HHMMSS

    def test_initialization_creates_state_file_path(self, temp_session_dir, monkeypatch):
        """Test initialization creates state file path."""
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)

        session = TradeSessionState()

        assert session.state_file is not None
        assert session.state_file.name.startswith("session_")
        assert session.state_file.suffix == ".json"

    def test_initialization_creates_directory(self, temp_session_dir, monkeypatch):
        """Test initialization creates session directory."""
        # Use a non-existent subdir so mkdir is needed
        new_dir = temp_session_dir / "new_subdir"
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", new_dir)

        session = TradeSessionState()

        assert new_dir.exists()


class TestSaveAndLoadState:
    """Test save_state and load_state methods."""

    def test_save_state_creates_file(self, temp_session_dir, sample_opportunities, monkeypatch):
        """Test save_state creates JSON file."""
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act
        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1],
            executed=[0],
            failed=[],
            metadata={"test": "value"},
        )

        # Assert
        assert session.state_file.exists()

    def test_save_state_writes_correct_json(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test save_state writes correct JSON data."""
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act
        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1],
            executed=[0],
            failed=[],
            metadata={"test": "value"},
        )

        # Assert
        data = json.loads(session.state_file.read_text())
        assert data["session_id"] == session.session_id
        assert data["phase"] == "execution"
        assert data["opportunities"] == sample_opportunities
        assert data["approved"] == [0, 1]
        assert data["executed"] == [0]
        assert data["failed"] == []
        assert data["metadata"] == {"test": "value"}

    def test_save_state_updates_current_state(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test save_state updates current_state attribute."""
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act
        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1],
            executed=[0],
            failed=[],
        )

        # Assert
        assert session.current_state is not None
        assert session.current_state.phase == "execution"
        assert session.current_state.approved == [0, 1]

    def test_load_state_reads_file(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test load_state reads state from file."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1],
            executed=[0],
            failed=[],
        )

        # Act
        loaded_state = session.load_state()

        # Assert
        assert loaded_state is not None
        assert loaded_state.phase == "execution"
        assert loaded_state.approved == [0, 1]
        assert loaded_state.executed == [0]

    def test_load_state_nonexistent_file(self, temp_session_dir, monkeypatch):
        """Test load_state returns None if file doesn't exist."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act
        result = session.load_state()

        # Assert
        assert result is None

    def test_save_load_round_trip(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test save and load state round-trip."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act
        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1, 2],
            executed=[0, 1],
            failed=[],
            metadata={"test": "value"},
        )

        loaded_state = session.load_state()

        # Assert
        assert loaded_state.phase == "execution"
        assert loaded_state.approved == [0, 1, 2]
        assert loaded_state.executed == [0, 1]
        assert loaded_state.failed == []
        assert loaded_state.metadata == {"test": "value"}


class TestFindIncompleteSessions:
    """Test find_incomplete_sessions class method."""

    def test_find_incomplete_sessions_finds_json_files(self, temp_session_dir):
        """Test finds .json files but not .complete.json files."""
        # Arrange
        (temp_session_dir / "session_001.json").write_text("{}")
        (temp_session_dir / "session_002.json").write_text("{}")
        (temp_session_dir / "session_003.complete.json").write_text("{}")

        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act
            incomplete = TradeSessionState.find_incomplete_sessions()

            # Assert
            assert len(incomplete) == 2
            assert all(f.suffix == ".json" for f in incomplete)
            assert all(".complete.json" not in f.name for f in incomplete)

    def test_find_incomplete_sessions_empty_directory(self, temp_session_dir):
        """Test returns empty list when no sessions exist."""
        # Arrange
        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act
            incomplete = TradeSessionState.find_incomplete_sessions()

            # Assert
            assert incomplete == []

    def test_find_incomplete_sessions_only_complete(self, temp_session_dir):
        """Test returns empty list when only complete sessions exist."""
        # Arrange
        (temp_session_dir / "session_001.complete.json").write_text("{}")
        (temp_session_dir / "session_002.complete.json").write_text("{}")

        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act
            incomplete = TradeSessionState.find_incomplete_sessions()

            # Assert
            assert incomplete == []

    def test_find_incomplete_sessions_nonexistent_directory(self, tmp_path):
        """Test returns empty list if directory doesn't exist."""
        # Arrange
        nonexistent = tmp_path / "nonexistent"

        with patch.object(TradeSessionState, "STATE_DIR", nonexistent):
            # Act
            incomplete = TradeSessionState.find_incomplete_sessions()

            # Assert
            assert incomplete == []

    def test_find_incomplete_sessions_sorted(self, temp_session_dir):
        """Test returns sorted list of sessions."""
        # Arrange
        (temp_session_dir / "session_003.json").write_text("{}")
        (temp_session_dir / "session_001.json").write_text("{}")
        (temp_session_dir / "session_002.json").write_text("{}")

        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act
            incomplete = TradeSessionState.find_incomplete_sessions()

            # Assert
            assert len(incomplete) == 3
            names = [f.name for f in incomplete]
            assert names == sorted(names)


class TestResumeSession:
    """Test resume_session class method."""

    def test_resume_session_loads_existing(
        self, temp_session_dir, sample_opportunities
    ):
        """Test resume_session loads existing session correctly."""
        # Arrange
        session_data = {
            "session_id": "20240115_123456",
            "timestamp": "2024-01-15T12:34:56",
            "phase": "execution",
            "opportunities": sample_opportunities,
            "approved": [0, 1],
            "executed": [0],
            "failed": [],
            "metadata": {"test": "value"},
        }

        session_file = temp_session_dir / "session_20240115_123456.json"
        session_file.write_text(json.dumps(session_data))

        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act
            session = TradeSessionState.resume_session(session_file)

            # Assert
            assert session.session_id == "20240115_123456"
            assert session.current_state.phase == "execution"
            assert session.current_state.approved == [0, 1]
            assert session.current_state.executed == [0]

    def test_resume_session_nonexistent_file(self, temp_session_dir):
        """Test resume_session raises ValueError if file doesn't exist."""
        # Arrange
        session_file = temp_session_dir / "nonexistent.json"

        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act & Assert
            with pytest.raises(ValueError, match="Session file not found"):
                TradeSessionState.resume_session(session_file)

    def test_resume_session_invalid_json(self, temp_session_dir):
        """Test resume_session raises ValueError if JSON is invalid."""
        # Arrange
        session_file = temp_session_dir / "invalid.json"
        session_file.write_text("invalid json")

        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act & Assert
            with pytest.raises(ValueError, match="Failed to resume session"):
                TradeSessionState.resume_session(session_file)

    def test_resume_session_missing_session_id(self, temp_session_dir):
        """Test resume_session raises ValueError if session_id missing."""
        # Arrange
        session_data = {
            "timestamp": "2024-01-15T12:34:56",
            "phase": "execution",
            "opportunities": [],
            "approved": [],
            "executed": [],
            "failed": [],
        }

        session_file = temp_session_dir / "invalid.json"
        session_file.write_text(json.dumps(session_data))

        with patch.object(TradeSessionState, "STATE_DIR", temp_session_dir):
            # Act & Assert
            with pytest.raises(ValueError, match="Invalid session file: missing session_id"):
                TradeSessionState.resume_session(session_file)


class TestMarkComplete:
    """Test mark_complete method."""

    def test_mark_complete_renames_file(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test mark_complete renames file to .complete.json."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1],
            executed=[0, 1],
            failed=[],
        )

        # Act
        session.mark_complete()

        # Assert
        assert not (temp_session_dir / f"session_{session.session_id}.json").exists()
        assert (temp_session_dir / f"session_{session.session_id}.complete.json").exists()

    def test_mark_complete_nonexistent_file(self, temp_session_dir, monkeypatch):
        """Test mark_complete handles nonexistent file gracefully."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act (should not raise exception)
        session.mark_complete()

        # Assert - no exception raised


class TestGetPendingExecutions:
    """Test get_pending_executions method."""

    def test_get_pending_executions_returns_pending(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test returns approved but not executed/failed indices."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1, 2],
            executed=[0],
            failed=[1],
        )

        # Act
        pending = session.get_pending_executions()

        # Assert
        assert pending == [2]  # Only index 2 is pending

    def test_get_pending_executions_all_executed(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test returns empty list when all are executed."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1, 2],
            executed=[0, 1, 2],
            failed=[],
        )

        # Act
        pending = session.get_pending_executions()

        # Assert
        assert pending == []

    def test_get_pending_executions_no_current_state(
        self, temp_session_dir, monkeypatch
    ):
        """Test returns empty list when no current state."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act
        pending = session.get_pending_executions()

        # Assert
        assert pending == []


class TestUpdateExecutionStatus:
    """Test update_execution_status method."""

    def test_update_execution_status_success(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test marking opportunity as executed."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1, 2],
            executed=[],
            failed=[],
        )

        # Act
        session.update_execution_status(index=0, success=True)

        # Assert
        assert 0 in session.current_state.executed
        assert 0 not in session.current_state.failed

    def test_update_execution_status_failure(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test marking opportunity as failed."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1, 2],
            executed=[],
            failed=[],
        )

        # Act
        session.update_execution_status(index=1, success=False, error_message="Test error")

        # Assert
        assert 1 in session.current_state.failed
        assert 1 not in session.current_state.executed
        assert session.current_state.metadata["error_1"] == "Test error"

    def test_update_execution_status_persists_state(
        self, temp_session_dir, sample_opportunities, monkeypatch
    ):
        """Test update_execution_status saves state to file."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        session.save_state(
            phase="execution",
            opportunities=sample_opportunities,
            approved=[0, 1, 2],
            executed=[],
            failed=[],
        )

        # Act
        session.update_execution_status(index=0, success=True)

        # Assert - reload from file and verify
        data = json.loads(session.state_file.read_text())
        assert 0 in data["executed"]

    def test_update_execution_status_no_current_state(
        self, temp_session_dir, monkeypatch
    ):
        """Test update_execution_status handles missing current_state."""
        # Arrange
        monkeypatch.setattr(TradeSessionState, "STATE_DIR", temp_session_dir)
        session = TradeSessionState()

        # Act (should not raise exception)
        session.update_execution_status(index=0, success=True)

        # Assert - no exception raised
