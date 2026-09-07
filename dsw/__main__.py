"""
__main__.py - CLI entry point.

Run as:  python -m dsw  or  dsw  (if installed via pip)
"""

import argparse
import atexit
import contextlib
import logging
import os
import shutil
import signal
import sys
import threading
import tomllib

from . import __version__, state
from . import ansi as _ansi_mod
from .data import fetcher_loop, stop_active_processes

# .loops is deliberately NOT imported here: it imports termios/tty at module
# level, which do not exist on Windows. Importing it before _preflight_checks()
# runs would replace the friendly win32 error with a ModuleNotFoundError
# traceback. main() imports it after the platform guard.

_CLI_EPILOG = """\
Keys:
  c / m / n   Sort by cpu / mem / name
  r           Reset sort + clear filter
  i           Toggle container ID column
  space       Pause/resume
  /           Filter (Enter to confirm, Esc to clear)
  Enter       Open/close detail view for selected row
  Esc         Close detail view / clear filter / deselect row
  Up/Down     Enable highlight / move row selection
  l           Toggle row highlight
  b           Cycle key bar: hint -> full -> off
  h           Help
  q           Quit

Flags:
  --sort KEY    Initial sort: cpu/mem/name/none
  --filter STR  Pre-set name filter on startup
  dsw NAME      Shortcut for dsw --filter NAME
  --runtime KEY Use docker or podman as the stats runtime
  --top N       Show only top N containers
  --highlight   Start with row highlight on
  --show-id     Show container ID column
  --no-color    Disable ANSI color output
  --hide-bar    Start with the bottom key bar hidden

Config:
  Persist any of the above as defaults in a TOML file at
  $XDG_CONFIG_HOME/dsw/config.toml (default ~/.config/dsw/config.toml), e.g.
  sort = "mem" / highlight = true. CLI flags override the config file.
  Run  dsw --init  to write a commented starter config.
"""


def _detect_no_color(args: argparse.Namespace) -> bool:
    """Return True if color output should be suppressed."""
    if args.no_color:
        return True
    if os.environ.get("NO_COLOR", "") != "":
        return True
    if os.environ.get("CLICOLOR", "") == "0":
        return True
    return os.environ.get("TERM", "") == "dumb"


def _detect_truecolor() -> bool:
    """Return True if the terminal advertises 24-bit (truecolor) support.

    Only COLORTERM=truecolor/24bit is treated as truecolor-capable; everything
    else falls back to 256-color so terminals without truecolor (e.g. macOS
    Terminal.app) render correct colors instead of mis-parsing the sequence.
    """
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _configure_logging(debug_file: str | None = None) -> None:
    """Configure package logging without using logging's stderr fallback."""
    logger = logging.getLogger("dsw")
    if debug_file:
        handler = logging.FileHandler(debug_file, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    elif not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())
    logger.propagate = False


def _preflight_checks() -> None:
    """Fail fast on unsupported platforms or environments."""
    if sys.platform == "win32":
        sys.exit("dsw: error: dsw requires a Unix-like terminal (Linux/macOS)")

    if not sys.stdin.isatty():
        sys.exit("dsw: error: stdin is not a TTY (dsw requires an interactive terminal)")

    if not sys.stdout.isatty():
        sys.exit("dsw: error: stdout is not a TTY (dsw requires an interactive terminal)")


_saved_terminal_attrs: list | None = None

# Set to the signal number when a signal triggers the clean-quit path, so main()
# can exit 128+signum (the shell convention) instead of masking it as success.
_exit_signal: int | None = None


def _signal_quit(signum: int, _frame: object) -> None:
    global _exit_signal
    _exit_signal = signum
    state.request_quit()


def _restore_terminal() -> None:
    """Safety-net terminal restoration registered via atexit."""
    global _saved_terminal_attrs
    if _saved_terminal_attrs is None:
        return
    # termios is guaranteed importable here: _saved_terminal_attrs is non-None
    # only because the tcgetattr in main() already imported it successfully.
    import termios

    with contextlib.suppress(OSError, ValueError):
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _saved_terminal_attrs)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dsw",
        description="A colorful, sortable, zero-dependency terminal dashboard for docker stats.",
        epilog=_CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"dsw {__version__}",
    )
    parser.add_argument(
        "--sort", choices=["cpu", "mem", "name", "none"],
        default="cpu", metavar="KEY",
        help="Initial sort column: cpu, mem, name, none (default: cpu)",
    )
    parser.add_argument(
        "--filter", default="", metavar="STR",
        help="Pre-set a container name filter on startup (substring, case-insensitive)",
    )
    parser.add_argument(
        "--runtime", choices=["docker", "podman"],
        default="docker", metavar="KEY",
        help="Container runtime to query: docker or podman (default: docker)",
    )
    parser.add_argument(
        "--highlight", action="store_true", default=False,
        help="Start with row highlight and arrow-key selection enabled",
    )
    parser.add_argument(
        "--top", type=int, default=0, metavar="N",
        help="Show only the top N containers by active sort key (0 = all, default: 0)",
    )
    parser.add_argument(
        "--show-id", action="store_true", default=False,
        help="Show the short container ID column on startup",
    )
    parser.add_argument(
        "--no-color", action="store_true", default=False,
        help="Disable ANSI color output (also honoured via NO_COLOR env var)",
    )
    parser.add_argument(
        "--hide-bar", action="store_true", default=False,
        help="Start with the bottom key bar hidden (toggle back with b)",
    )
    parser.add_argument(
        "--init", action="store_true", default=False,
        help="Write a commented config template to the config path and exit",
    )
    parser.add_argument(
        "--debug", metavar="FILE", default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--replay", metavar="FILE", default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "filter_terms",
        nargs="*",
        metavar="FILTER",
        help="Container name filter shortcut, equivalent to --filter",
    )
    return parser


def _normalize_filter_args(args: argparse.Namespace) -> None:
    if not args.filter and args.filter_terms:
        args.filter = " ".join(args.filter_terms)


# Options a user may persist in the config file. Excludes one-shot/hidden flags
# (--version, --debug, --replay) and the positional filter shortcut.
_CONFIG_KEYS = frozenset(
    {"sort", "filter", "runtime", "highlight", "top", "show_id", "no_color", "hide_bar"}
)


def _config_path() -> str:
    """Path to the TOML config, honouring XDG_CONFIG_HOME (default ~/.config)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "dsw", "config.toml")


def _load_config() -> dict:
    """Read persisted defaults from the TOML config; never fatal.

    A missing file is silently ignored. A malformed file or an unknown key
    warns on stderr and is skipped rather than crashing the app.
    """
    try:
        with open(_config_path(), "rb") as fh:
            cfg = tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as err:
        print(f"dsw: ignoring config file ({err})", file=sys.stderr)
        return {}
    for key in set(cfg) - _CONFIG_KEYS:
        print(f"dsw: ignoring unknown config key '{key}'", file=sys.stderr)
    return {key: cfg[key] for key in cfg if key in _CONFIG_KEYS}


# Starter config: every option present but commented out, so the file itself
# documents the available keys, their defaults, and allowed values.
_CONFIG_TEMPLATE = """\
# dsw config file. Uncomment a line to override the default.
# CLI flags always take precedence over this file.
# See the "Configuration file" section of the dsw README for details.

# sort = "cpu"        # initial sort column: cpu | mem | name | none
# filter = ""         # pre-set name filter (substring, case-insensitive)
# runtime = "docker"  # container runtime: docker | podman
# top = 0             # show only the top N containers (0 = all)
# highlight = false   # start with row highlight and arrow-key selection
# show_id = false     # show the short container ID column
# no_color = false    # disable ANSI color output
# hide_bar = false    # start with the bottom key bar hidden (toggle with b)
"""


def _write_config_template() -> None:
    """Write the starter config template, then exit. Never clobbers an existing
    file; the "x" open mode is the real guard against an overwrite race."""
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(_CONFIG_TEMPLATE)
    except FileExistsError:
        sys.exit(f"dsw: config already exists at {path} (not overwriting)")
    print(f"dsw: wrote config template to {path}")
    sys.exit(0)


def _config_to_argv(cfg: dict) -> list[str]:
    """Turn config values into argv tokens so argparse validates and overrides
    them. Prepended before the real args, so explicit CLI flags always win and
    bad values raise the normal argparse error (type/choices enforced)."""
    argv: list[str] = []
    for key, val in cfg.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(val, bool):
            if val:  # store_true flags: only the True case emits anything
                argv.append(flag)
        else:
            argv += [flag, str(val)]
    return argv


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    state.S.args = parser.parse_args([*_config_to_argv(_load_config()), *argv])

    # --init writes the starter config and exits; it needs no runtime or TTY.
    if state.S.args.init:
        _write_config_template()

    _normalize_filter_args(state.S.args)
    _configure_logging(state.S.args.debug)

    if state.S.args.top < 0:
        parser.error("--top must be a non-negative integer")

    # Validate the data source before the UI takes over the terminal. A missing
    # --replay file and an absent runtime binary both raise FileNotFoundError
    # mid-loop, where they are indistinguishable; checking here gives an
    # accurate, fail-fast message instead of a misleading "CLI not found".
    if state.S.args.replay is not None:
        if not os.path.exists(state.S.args.replay):
            parser.error(f"--replay file not found: {state.S.args.replay}")
    elif shutil.which(state.S.args.runtime) is None:
        parser.error(f"{state.S.args.runtime} CLI not found on PATH")

    _preflight_checks()

    # Safe only after the win32 guard above: loops.py needs termios/tty.
    from .loops import draw_loop, keyboard_loop

    # Apply parsed args to shared state.
    state.S.sort_key   = state.S.args.sort
    state.S.filter_str = state.S.args.filter
    state.S.top_n      = state.S.args.top
    state.S.highlight  = state.S.args.highlight
    state.S.show_id    = state.S.args.show_id
    if state.S.args.hide_bar:
        state.S.bar_mode = state.BAR_OFF

    no_color = _detect_no_color(state.S.args)
    _ansi_mod._no_color = no_color
    _ansi_mod._truecolor = _detect_truecolor()

    # Save terminal attrs for atexit safety net before threads modify them.
    # termios is already imported (loops.py above requires it on this platform),
    # so only the tcgetattr syscall can fail here.
    global _saved_terminal_attrs
    try:
        import termios
        _saved_terminal_attrs = termios.tcgetattr(sys.stdin.fileno())
    except OSError:
        pass
    atexit.register(_restore_terminal)
    atexit.register(stop_active_processes)

    # Ctrl+C (SIGINT) sets _quit cleanly, same as pressing "q".
    # This covers the window before tty.setcbreak() and any non-keyboard thread.
    # SIGTERM (kill, shutdown) and SIGHUP (pane/terminal close) take the same
    # clean-quit path so the atexit termios restore and child reaping still run
    # instead of leaving the shell in cbreak mode. SIGHUP exists on all
    # supported platforms (Windows is unsupported). The handler records the
    # signal so main() can exit 128+signum rather than 0 (pressing "q" exits 0).
    for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(_sig, _signal_quit)

    # Terminal resize (SIGWINCH): flag the next frame for a full repaint so the
    # in-place diff is not applied against a layout that has since rewrapped.
    # Signals can only be installed from the main thread, hence here.
    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, lambda _sig, _frame: state.request_redraw())

    threads: list[threading.Thread] = []
    for target in (fetcher_loop, keyboard_loop, draw_loop):
        t = threading.Thread(target=target, daemon=True)
        t.start()
        threads.append(t)

    state.S.quit.wait()

    stop_active_processes()

    # Give threads a moment to clean up.
    for t in threads:
        t.join(timeout=1.0)

    sys.stdout.write("\n")
    sys.stdout.flush()

    # A signal-triggered quit exits 128+signum (e.g. 130 for SIGINT, 143 for
    # SIGTERM) so shells and scripts can tell it apart from a clean "q" (exit 0).
    if _exit_signal is not None:
        raise SystemExit(128 + _exit_signal)


if __name__ == "__main__":
    main()
