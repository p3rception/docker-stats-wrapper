import argparse
import logging
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dsw import __main__ as main_mod
from dsw import __version__, state
from dsw.__main__ import (
    _build_parser,
    _config_to_argv,
    _configure_logging,
    _detect_no_color,
    _detect_truecolor,
    _load_config,
    _normalize_filter_args,
    _signal_quit,
    _write_config_template,
)

ROOT = Path(__file__).resolve().parents[1]


def test_main_module_imports_without_termios():
    # On Windows there is no termios/tty, so dsw.__main__ must be importable
    # without them for the win32 guard in _preflight_checks() to be reachable
    # (F-201). Blocking the modules via sys.modules simulates that platform.
    code = (
        "import sys; sys.modules['termios'] = None; sys.modules['tty'] = None; "
        "import dsw.__main__; print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_readme_documents_every_visible_cli_flag():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for action in _build_parser()._actions:
        if action.help == argparse.SUPPRESS:
            continue
        for option in action.option_strings:
            assert option in readme, f"README.md does not mention {option}"


def run_dsw(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "dsw", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def test_help_flag_prints_help_and_exits_zero():
    result = run_dsw("--help")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Keys:" in result.stdout
    assert "q           Quit" in result.stdout


def test_version_does_not_require_tty():
    result = run_dsw("--version")

    assert result.returncode == 0
    assert result.stdout.strip() == f"dsw {__version__}"
    assert "stdin is not a TTY" not in result.stderr


def test_signal_quit_records_signal_for_nonzero_exit():
    # A signal-triggered quit must be distinguishable from pressing "q": the
    # handler records the signal so main() can exit 128+signum.
    main_mod._exit_signal = None
    try:
        _signal_quit(signal.SIGTERM, None)
        assert main_mod._exit_signal == signal.SIGTERM
        assert state.S.quit.is_set()
        assert 128 + main_mod._exit_signal == 143  # SIGTERM -> 143
    finally:
        main_mod._exit_signal = None


def test_missing_replay_file_is_not_reported_as_missing_cli():
    # F-202: a missing --replay file must not be conflated with an absent
    # runtime binary. The diagnostic should name the file, not the CLI.
    result = run_dsw("--replay", "does-not-exist.jsonl")

    assert result.returncode == 2
    assert "CLI not found on PATH" not in result.stderr
    assert "--replay file not found: does-not-exist.jsonl" in result.stderr


def test_missing_runtime_binary_fails_fast_with_clear_message():
    # F-202: an absent runtime binary is knowable at startup, before the UI
    # takes over the terminal.
    result = run_dsw("--runtime", "podman")

    if shutil.which("podman") is not None:
        pytest.skip("podman is installed; cannot exercise the missing-binary path")

    assert result.returncode == 2
    assert "podman CLI not found on PATH" in result.stderr


def test_parser_accepts_podman_runtime():
    args = _build_parser().parse_args(["--runtime", "podman"])

    assert args.runtime == "podman"


def test_parser_accepts_hidden_replay_file():
    parser = _build_parser()
    args = parser.parse_args(["--replay", "fixture.jsonl"])

    assert args.replay == "fixture.jsonl"
    assert "--replay" not in parser.format_help()


def test_positional_container_name_sets_filter_shortcut():
    parser = _build_parser()
    args = parser.parse_args(["nginx"])

    _normalize_filter_args(args)

    assert args.filter == "nginx"


def test_multiple_positional_filter_terms_are_joined():
    parser = _build_parser()
    args = parser.parse_args(["web", "prod"])

    _normalize_filter_args(args)

    assert args.filter == "web prod"


def test_explicit_filter_takes_precedence_over_positional_shortcut():
    parser = _build_parser()
    args = parser.parse_args(["--filter", "api", "nginx"])

    _normalize_filter_args(args)

    assert args.filter == "api"


# -- _detect_no_color (F-6 / correctness) ------------------------------------

def _args(no_color: bool = False) -> SimpleNamespace:
    return SimpleNamespace(no_color=no_color)


def test_detect_no_color_flag():
    assert _detect_no_color(_args(no_color=True)) is True


def test_detect_no_color_default_is_false(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NO_COLOR",  raising=False)
    monkeypatch.delenv("CLICOLOR",  raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _detect_no_color(_args()) is False


def test_detect_no_color_env_no_color(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert _detect_no_color(_args()) is True


def test_detect_no_color_env_no_color_empty_string(monkeypatch: pytest.MonkeyPatch):
    # NO_COLOR="" (empty string) means color IS enabled per the spec.
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.delenv("CLICOLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _detect_no_color(_args()) is False


def test_detect_no_color_clicolor_zero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("CLICOLOR", "0")
    assert _detect_no_color(_args()) is True


def test_detect_no_color_term_dumb(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NO_COLOR",  raising=False)
    monkeypatch.delenv("CLICOLOR",  raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert _detect_no_color(_args()) is True


# -- _detect_truecolor (F-406) -----------------------------------------------

def test_detect_truecolor_colorterm_truecolor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert _detect_truecolor() is True


def test_detect_truecolor_colorterm_24bit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COLORTERM", "24bit")
    assert _detect_truecolor() is True


def test_detect_truecolor_default_is_false(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COLORTERM", raising=False)
    assert _detect_truecolor() is False


def test_detect_truecolor_other_colorterm_is_false(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COLORTERM", "16color")
    assert _detect_truecolor() is False


def test_configure_logging_suppresses_package_stderr_fallback():
    logger = logging.getLogger("dsw")
    original_handlers = list(logger.handlers)
    original_propagate = logger.propagate
    logger.handlers.clear()
    logger.propagate = True

    try:
        _configure_logging()

        assert any(isinstance(handler, logging.NullHandler) for handler in logger.handlers)
        assert logger.propagate is False
    finally:
        logger.handlers[:] = original_handlers
        logger.propagate = original_propagate


def test_configure_logging_can_write_debug_file(tmp_path: Path) -> None:
    logger = logging.getLogger("dsw")
    child = logging.getLogger("dsw.data")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers.clear()
    logger.propagate = True
    log_path = tmp_path / "dsw.log"

    try:
        _configure_logging(str(log_path))
        child.debug("diagnostic line")
        for handler in logger.handlers:
            handler.flush()

        assert "diagnostic line" in log_path.read_text()
        assert logger.propagate is False
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate


def _write_config(tmp_path, monkeypatch, body: str) -> None:
    cfg_dir = tmp_path / "dsw"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(body)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_load_config_missing_is_empty(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert _load_config() == {}


def test_load_config_filters_unknown_keys(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys):
    _write_config(tmp_path, monkeypatch, 'sort = "mem"\nbogus = 1\n')
    assert _load_config() == {"sort": "mem"}
    assert "unknown config key 'bogus'" in capsys.readouterr().err


def test_load_config_malformed_warns_not_fatal(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys):
    _write_config(tmp_path, monkeypatch, "sort = ")  # invalid TOML
    assert _load_config() == {}
    assert "ignoring config file" in capsys.readouterr().err


def test_config_to_argv_bools_and_values():
    argv = _config_to_argv({"sort": "mem", "highlight": True, "show_id": False, "top": 5})
    assert argv == ["--sort", "mem", "--highlight", "--top", "5"]


def test_config_sets_defaults_cli_overrides(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _write_config(tmp_path, monkeypatch, 'sort = "mem"\nhighlight = true\n')
    parser = _build_parser()
    base = parser.parse_args([*_config_to_argv(_load_config()), *[]])
    assert base.sort == "mem" and base.highlight is True
    # explicit flag wins over config
    override = parser.parse_args([*_config_to_argv(_load_config()), "--sort", "cpu"])
    assert override.sort == "cpu" and override.highlight is True


def test_config_invalid_value_is_validated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _write_config(tmp_path, monkeypatch, 'sort = "bogus"\n')
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([*_config_to_argv(_load_config())])


def test_init_writes_commented_template(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        _write_config_template()
    assert exc.value.code == 0
    cfg = tmp_path / "dsw" / "config.toml"
    assert cfg.exists()
    assert str(cfg) in capsys.readouterr().out
    # Template is valid TOML and fully commented, so it parses to no overrides.
    assert _load_config() == {}


def test_init_refuses_to_clobber_existing(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _write_config(tmp_path, monkeypatch, 'sort = "mem"\n')
    with pytest.raises(SystemExit) as exc:
        _write_config_template()
    assert "already exists" in str(exc.value)
    assert _load_config() == {"sort": "mem"}  # original left untouched


def test_hide_bar_config_flows_to_parsed_arg(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _write_config(tmp_path, monkeypatch, "hide_bar = true\n")
    parser = _build_parser()
    args = parser.parse_args([*_config_to_argv(_load_config())])
    assert args.hide_bar is True
