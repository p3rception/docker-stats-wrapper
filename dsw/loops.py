"""
loops.py - draw loop and keyboard loop threads.

draw_loop()     repaints the terminal whenever _dirty is set.
keyboard_loop() reads raw keystrokes and updates shared state.
"""

import os
import select
import sys
import termios
import tty
from codecs import getincrementaldecoder

from . import state
from .render import _build_frame, _build_help_frame

_COMMAND_KEY_BY_POSITION = str.maketrans({
    # Greek keyboard layout, physical QWERTY positions.
    ";": "q",
    "ς": "w",
    "ε": "e",
    "ρ": "r",
    "τ": "t",
    "υ": "y",
    "θ": "u",
    "ι": "i",
    "ο": "o",
    "π": "p",
    "α": "a",
    "σ": "s",
    "δ": "d",
    "φ": "f",
    "γ": "g",
    "η": "h",
    "ξ": "j",
    "κ": "k",
    "λ": "l",
    "ζ": "z",
    "χ": "x",
    "ψ": "c",
    "ω": "v",
    "β": "b",
    "ν": "n",
    "μ": "m",
    # Russian Cyrillic keyboard layout, physical QWERTY positions.
    "й": "q",
    "ц": "w",
    "у": "e",
    "к": "r",
    "е": "t",
    "н": "y",
    "г": "u",
    "ш": "i",
    "щ": "o",
    "з": "p",
    "х": "[",
    "ъ": "]",
    "ф": "a",
    "ы": "s",
    "в": "d",
    "а": "f",
    "п": "g",
    "р": "h",
    "о": "j",
    "л": "k",
    "д": "l",
    "ж": ";",
    "э": "'",
    "я": "z",
    "ч": "x",
    "с": "c",
    "м": "v",
    "и": "b",
    "т": "n",
    "ь": "m",
    "б": ",",
    "ю": ".",
    ".": "/",
})


def _normalize_command_key(ch: str) -> str:
    """Return the US-keyboard command key for a command-mode keypress."""
    if len(ch) != 1:
        return ch
    return ch.lower().translate(_COMMAND_KEY_BY_POSITION)


def _split_escape_sequences(seq: str) -> list[str]:
    """Split a coalesced escape buffer into individual sequences.

    Terminal key repeat can deliver several presses inside one read window
    (e.g. ``"\\x1b[B\\x1b[B"`` when an arrow is held).  Split on ESC and
    re-prefix each non-empty part so each sequence is applied in order
    (F-404).  A bare ESC (``"\\x1b"``) splits to nothing, so fall back to
    the whole buffer to preserve the detail-view/clear-selection behaviour.
    """
    return ["\x1b" + p for p in seq.split("\x1b") if p] or [seq]


def _apply_escape_sequence(seq: str) -> bool:
    """Apply a terminal escape sequence. Return True when display state changed."""
    with state.S.lock:
        highlight = state.S.highlight
        visible_ids = list(state.S.visible_ids)
        old_sel_id = state.S.sel_id
        detail_view = state.S.detail_view

    new_sel_id = old_sel_id
    arrow_seq = seq in ("\x1b[A", "\x1b[B")
    highlight_changed = False

    if arrow_seq and not highlight:
        highlight = True
        highlight_changed = True
        if new_sel_id == "" and visible_ids:
            new_sel_id = visible_ids[0]
    elif seq == "\x1b[A" and highlight:
        if visible_ids:
            cur = visible_ids.index(old_sel_id) if old_sel_id in visible_ids else -1
            cur = max(0, cur - 1) if cur > 0 else 0
            new_sel_id = visible_ids[cur]
    elif seq == "\x1b[B" and highlight:
        if visible_ids:
            cur = visible_ids.index(old_sel_id) if old_sel_id in visible_ids else -1
            cur = min(len(visible_ids) - 1, cur + 1 if cur >= 0 else 0)
            new_sel_id = visible_ids[cur]
    elif seq == "\x1b":
        if detail_view:
            with state.S.lock:
                state.S.detail_view = False
            return True
        new_sel_id = ""

    if new_sel_id == old_sel_id and not highlight_changed:
        return False

    with state.S.lock:
        if highlight_changed:
            state.S.highlight = True
        state.S.sel_id = new_sel_id
    return True


def _apply_enter_key() -> bool:
    """Open detail view for the selected visible row."""
    with state.S.lock:
        selected = state.S.sel_id
        if not state.S.visible_ids:
            return False
        if not selected or selected not in state.S.visible_ids:
            selected = state.S.visible_ids[0]
            state.S.sel_id = selected
        if state.S.detail_view:
            state.S.detail_view = False
            return True
        state.S.detail_view = True
        state.S.details.pop(selected, None)
    return True


def _next_bar_mode(mode: int) -> int:
    # ponytail: hint is one-way; once dismissed it never returns
    if mode == state.BAR_HINT:
        return state.BAR_FULL
    return state.BAR_FULL if mode == state.BAR_OFF else state.BAR_OFF


def _apply_filter_key(ch: str) -> None:
    """Handle one keystroke while the interactive filter is being typed.

    Quitting is intentionally limited to Ctrl-C here: any printable letter -
    including ``q``/``Q`` - is appended to the filter so names like ``rabbitmq``
    can be typed (F-403).
    """
    if ch in ("\r", "\n"):          # Enter - confirm
        with state.S.lock:
            state.S.filter_typing = False
    elif ch == "\x1b":              # Esc - cancel and clear
        with state.S.lock:
            state.S.filter_typing = False
            state.S.filter_str    = ""
            state.S.filter_cursor = 0
    elif ch == "\x1b[D":            # Left arrow - move within filter text
        with state.S.lock:
            state.S.filter_cursor = max(0, min(state.S.filter_cursor, len(state.S.filter_str)) - 1)
    elif ch == "\x1b[C":            # Right arrow - move within filter text
        with state.S.lock:
            state.S.filter_cursor = min(len(state.S.filter_str), max(0, state.S.filter_cursor) + 1)
    elif ch == "\x1b[3~":           # Delete - remove character after cursor
        with state.S.lock:
            cursor = max(0, min(state.S.filter_cursor, len(state.S.filter_str)))
            if cursor < len(state.S.filter_str):
                state.S.filter_str = state.S.filter_str[:cursor] + state.S.filter_str[cursor + 1:]
            state.S.filter_cursor = cursor
    elif ch in ("\x7f", "\x08"):    # Backspace / Delete
        with state.S.lock:
            cursor = max(0, min(state.S.filter_cursor, len(state.S.filter_str)))
            if cursor > 0:
                state.S.filter_str = state.S.filter_str[:cursor - 1] + state.S.filter_str[cursor:]
                state.S.filter_cursor = cursor - 1
            else:
                state.S.filter_cursor = 0
    elif ch == "\x03":              # Ctrl-C - quit even while typing
        state.request_quit()
    elif ch.isprintable():
        with state.S.lock:
            cursor = max(0, min(state.S.filter_cursor, len(state.S.filter_str)))
            state.S.filter_str = state.S.filter_str[:cursor] + ch + state.S.filter_str[cursor:]
            state.S.filter_cursor = cursor + len(ch)


def draw_loop() -> None:
    last_output  = b""
    last_rows    = 0   # visual terminal rows occupied by the last frame
    stdout       = sys.stdout.buffer  # write bytes directly - skip encode step

    while not state.S.quit.is_set():
        # Block until the fetcher or a keypress marks the display dirty.
        # No FPS cap - we just follow docker stats cadence (~1 Hz).
        state.S.dirty.wait()
        if state.S.quit.is_set():
            break
        state.S.dirty.clear()

        # On resize the previous frame has rewrapped at the old width, so both
        # the cached last_output (used to skip identical frames) and last_rows
        # (the cursor-up count) describe a layout that no longer exists. Clear
        # the screen and repaint from the top rather than diffing against it.
        resized = state.S.resized.is_set()
        if resized:
            state.S.resized.clear()
            stdout.write(b"\x1b[2J\x1b[H")
            stdout.flush()
            last_output = b""
            last_rows   = 0

        try:
            size   = os.get_terminal_size()
            term_w = size.columns
            term_h = size.lines
        except OSError:
            term_w = 80
            term_h = 24

        with state.S.lock:
            show_help = state.S.show_help

        if show_help:
            output, row_count = _build_help_frame(term_w)
            visible_ids: list[str] = []
        else:
            output, row_count, visible_ids = _build_frame(term_w, term_h)

        if output == last_output:
            continue
        last_output = output

        # Publish display order for the keyboard loop under the lock.
        # Done here (in draw_loop) so _build_frame stays side-effect free.
        if not show_help:
            with state.S.lock:
                state.S.visible_ids[:] = visible_ids

        # Frames end on a trailing blank line with no terminating newline, so
        # after writing N visual rows the cursor sits on the last row - N-1
        # rows below the top, not N. Moving up the full N overshoots by one
        # every tick, creeping the frame upward and eating a scrollback row
        # each refresh on terminals without Warp-style blocks. Move up N-1.
        # (ESC[0A is treated as ESC[1A, so guard against single-row frames.)
        if last_rows > 1:
            stdout.write(f"\x1b[{last_rows - 1}A\x1b[J".encode())
        stdout.write(output)
        # Erase from cursor to end of screen after writing the new frame.
        # This cleans up any leftover rows when the new frame is shorter
        # than the previous one (e.g. help -> stats transition).
        stdout.write(b"\x1b[J")
        stdout.flush()
        last_rows = row_count


def keyboard_loop() -> None:
    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    decoder = getincrementaldecoder("utf-8")(errors="replace")
    try:
        tty.setcbreak(fd)
        while not state.S.quit.is_set():
            if not select.select([fd], [], [], 0.5)[0]:
                continue
            data = os.read(fd, 1)
            if not data:
                # Empty read means stdin EOF (e.g. TTY hangup). select() would
                # report the fd readable forever, so without quitting here the
                # loop spins at 100% CPU. An empty os.read is genuine EOF;
                # a partial multibyte input yields non-empty bytes that decode
                # to "" instead, which the `if not ch` branch below handles.
                state.request_quit()
                break
            ch = decoder.decode(data)
            if not ch:
                continue
            if state.S.filter_typing:
                if ch == "\x1b":
                    seq = ch
                    while select.select([fd], [], [], 0.05)[0]:
                        seq += decoder.decode(os.read(fd, 1))
                    for one in _split_escape_sequences(seq):
                        _apply_filter_key(one)
                else:
                    _apply_filter_key(ch)
                state.S.dirty.set()
                continue

            key = _normalize_command_key(ch)
            if key in ("q", "\x03"):
                state.request_quit()
            elif key == "c":
                with state.S.lock:
                    state.S.sort_asc = (not state.S.sort_asc) if state.S.sort_key == "cpu" else False
                    state.S.sort_key = "cpu"
                state.S.dirty.set()
            elif key == "m":
                with state.S.lock:
                    state.S.sort_asc = (not state.S.sort_asc) if state.S.sort_key == "mem" else False
                    state.S.sort_key = "mem"
                state.S.dirty.set()
            elif key == "n":
                with state.S.lock:
                    state.S.sort_asc = (not state.S.sort_asc) if state.S.sort_key == "name" else False
                    state.S.sort_key = "name"
                state.S.dirty.set()
            elif key == "r":
                with state.S.lock:
                    state.S.sort_key      = "none"
                    state.S.sort_asc      = False
                    state.S.filter_str    = ""
                    state.S.filter_typing = False
                    state.S.filter_cursor = 0
                state.S.dirty.set()
            elif key == "i":
                with state.S.lock:
                    state.S.show_id = not state.S.show_id
                state.S.dirty.set()
            elif key == "h":
                with state.S.lock:
                    state.S.show_help = not state.S.show_help
                state.S.dirty.set()
            elif key in ("\r", "\n"):
                if _apply_enter_key():
                    state.S.dirty.set()
            elif key == "b":
                with state.S.lock:
                    state.S.bar_mode = _next_bar_mode(state.S.bar_mode)
                state.S.dirty.set()
            elif key == " ":
                with state.S.lock:
                    state.S.paused = not state.S.paused
                    if state.S.paused:
                        # take a snapshot of current data so the display freezes
                        state.S.paused_rows[:] = list(state.S.rows)
                        state.S.paused_ts = state.S.last_ts
                state.S.dirty.set()
            elif key == "/":
                with state.S.lock:
                    state.S.filter_typing = True
                    state.S.filter_cursor = len(state.S.filter_str)
                state.S.dirty.set()
            elif key == "l":
                with state.S.lock:
                    state.S.highlight = not state.S.highlight
                    if state.S.highlight and state.S.sel_id == "":
                        # auto-select first row by id
                        src = state.S.paused_rows if state.S.paused else state.S.rows
                        state.S.sel_id = src[0].id if src else ""
                    elif not state.S.highlight:
                        state.S.sel_id = ""    # clear selection when disabling
                state.S.dirty.set()
            elif key == "\x1b":            # ESC or start of escape seq
                # Accumulate the full escape sequence into a buffer before
                # matching.  This ensures unrecognised sequences (e.g. page-up
                # "\x1b[5~") are consumed completely rather than leaving
                # residual bytes that would be mis-processed as key commands.
                seq = ch
                while select.select([fd], [], [], 0.05)[0]:
                    seq += decoder.decode(os.read(fd, 1))
                # Apply each coalesced sequence in order so held arrows
                # scroll repeatedly instead of being dropped (F-404).
                changed = False
                for one in _split_escape_sequences(seq):
                    changed = _apply_escape_sequence(one) or changed
                if changed:
                    state.S.dirty.set()
                # All other sequences (e.g. page-up/down, F-keys) are silently
                # ignored; they have already been fully consumed by the loop above.
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
