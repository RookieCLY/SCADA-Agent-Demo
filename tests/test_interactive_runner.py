from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.interactive_runner import (
	RunnerSession,
	create_session,
	handle_command,
	load_config_into_session,
	load_world_json,
	main,
)


@pytest.mark.mock_only
def test_startup_status_command_shows_acceptance_fields(tmp_path: Path, capsys):
	code = main(
		[
			"--provider",
			"mock",
			"--model",
			"mock",
			"--results-root",
			str(tmp_path / "results"),
			"--command",
			"help",
		]
	)

	out = capsys.readouterr().out
	assert code == 0
	assert "SCADA Interactive Runner" in out
	assert "Current config" in out
	assert "Current model  : mock/mock" in out
	assert "Current world" in out
	assert "Show LLM IO" in out
	assert "golden" in out
	assert "query" in out


@pytest.mark.mock_only
def test_world_add_save_and_load_json(tmp_path: Path):
	session = RunnerSession(results_root=tmp_path / "results")

	ok, msg, _ = handle_command(session, "world reset")
	assert ok, msg
	ok, msg, _ = handle_command(session, "world add point TEMP_201 analog °C 0 200")
	assert ok, msg
	ok, msg, _ = handle_command(session, "world add page p1 Main 1920 1080 #000000")
	assert ok, msg
	ok, msg, _ = handle_command(session, "world add widget p1 w1 thermometer 10 20 80 200")
	assert ok, msg

	ok, msg, _ = handle_command(session, "inspect world")
	assert ok, msg
	assert "points       : 1 analog" in msg
	assert "pages        : 1" in msg
	assert "widgets      : 1 total" in msg

	world_path = tmp_path / "world.json"
	ok, msg, _ = handle_command(session, f"world save-json {world_path}")
	assert ok, msg
	loaded = load_world_json(world_path)
	assert "TEMP_201" in loaded.points
	assert "p1" in loaded.pages
	assert "w1" in loaded.pages["p1"].widgets

	new_session = RunnerSession(results_root=tmp_path / "results2")
	ok, msg, _ = handle_command(new_session, f"world load-json {world_path}")
	assert ok, msg
	assert "TEMP_201" in new_session.world.points


@pytest.mark.mock_only
def test_golden_load_initializes_world_and_query_writes_trace(tmp_path: Path):
	session = RunnerSession(results_root=tmp_path / "results")
	session.dataset_path = Path("eval/golden_dataset.jsonl")
	ok, msg = load_config_into_session(session, Path("configs/D_minimal.yaml"))
	assert ok, msg

	ok, msg, _ = handle_command(session, "golden golden-001")
	assert ok, msg
	assert session.current_golden is not None
	assert session.current_golden.id == "golden-001"
	assert not session.world.points
	assert not session.world.pages
	assert not session.world.alarms
	assert "expected_behavior" in msg

	ok, msg, _ = handle_command(session, "query 给TEMP_101加个高温报警,超过80度告警")
	assert ok, msg
	assert session.last_trace is not None
	assert session.agent is not None
	assert session.agent.tracer.traces_path.exists()
	assert "trace_id" in msg


@pytest.mark.mock_only
def test_llm_switch_unsupported_keeps_previous_agent(tmp_path: Path):
	session = RunnerSession(results_root=tmp_path / "results")
	ok, msg = load_config_into_session(session, Path("configs/D_minimal.yaml"))
	assert ok, msg
	old_agent = session.agent

	ok, msg, _ = handle_command(session, "llm openai gpt-4o")
	assert not ok
	assert "Unsupported provider" in msg
	assert session.agent is old_agent
	assert session.provider == "mock"
	assert session.model == "mock"


@pytest.mark.mock_only
def test_display_toggles_and_event_sink_prints_llm_output(tmp_path: Path, capsys):
	session = RunnerSession(results_root=tmp_path / "results")
	ok, msg = load_config_into_session(session, Path("configs/D_minimal.yaml"))
	assert ok, msg
	ok, msg, _ = handle_command(session, "world demo")
	assert ok, msg
	ok, msg, _ = handle_command(session, "display llm-output on")
	assert ok, msg
	assert session.show_llm_output is True
	ok, msg, _ = handle_command(session, "display world off")
	assert ok, msg
	assert session.show_world_realtime is False

	ok, msg, _ = handle_command(session, "query 给TEMP_101加个高温报警,超过80度告警")
	captured = capsys.readouterr().out
	assert ok, msg
	assert "[assistant]" in captured
	assert "World changed:" not in captured
	assert "alarms" in session.world.snapshot()


@pytest.mark.mock_only
def test_config_failure_keeps_previous_config(tmp_path: Path):
	session = RunnerSession(results_root=tmp_path / "results")
	ok, msg = load_config_into_session(session, Path("configs/D_minimal.yaml"))
	assert ok, msg
	old_config = session.config
	old_agent = session.agent

	ok, msg, _ = handle_command(session, "config configs/does-not-exist.yaml")
	assert not ok
	assert "Config load failed" in msg
	assert session.config is old_config
	assert session.agent is old_agent


@pytest.mark.mock_only
def test_create_session_accepts_startup_flags(tmp_path: Path):
	args = SimpleNamespace(
		config="configs/D_minimal.yaml",
		dataset="eval/golden_dataset.jsonl",
		provider="mock",
		model="mock",
		results_root=str(tmp_path / "results"),
		no_world_realtime=True,
		show_llm_output=True,
		show_reasoning=True,
	)

	session = create_session(args)

	assert session.agent is not None
	assert session.show_world_realtime is False
	assert session.show_llm_output is True
	assert session.show_llm_reasoning is True
	assert session.golden_records
