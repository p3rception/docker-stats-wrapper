"""test_loops.py - unit tests for keyboard_loop helpers."""

from typing import ClassVar

from dsw import loops, state


def _apply_seq(seq: str, highlight: bool, visible_ids: list[str], sel_id: str) -> tuple[str, bool]:
    state.S.highlight = highlight
    state.S.visible_ids = visible_ids
    state.S.sel_id = sel_id

    changed = loops._apply_escape_sequence(seq)

    return state.S.sel_id, changed


def _apply_seq_with_highlight(
    seq: str,
    highlight: bool,
    visible_ids: list[str],
    sel_id: str,
) -> tuple[str, bool, bool]:
    state.S.highlight = highlight
    state.S.visible_ids = visible_ids
    state.S.sel_id = sel_id

    changed = loops._apply_escape_sequence(seq)

    return state.S.sel_id, state.S.highlight, changed


class TestArrowNavigation:
    IDS: ClassVar[list[str]] = ["a", "b", "c"]

    def test_up_from_second_moves_to_first(self):
        result, changed = _apply_seq("\x1b[A", highlight=True, visible_ids=self.IDS, sel_id="b")
        assert result == "a"
        assert changed is True

    def test_down_from_first_moves_to_second(self):
        result, changed = _apply_seq("\x1b[B", highlight=True, visible_ids=self.IDS, sel_id="a")
        assert result == "b"
        assert changed is True

    def test_up_at_top_stays_at_top(self):
        result, changed = _apply_seq("\x1b[A", highlight=True, visible_ids=self.IDS, sel_id="a")
        assert result == "a"
        assert changed is False

    def test_down_at_bottom_stays_at_bottom(self):
        result, changed = _apply_seq("\x1b[B", highlight=True, visible_ids=self.IDS, sel_id="c")
        assert result == "c"
        assert changed is False

    def test_arrow_with_no_selection_starts_at_first(self):
        # sel_id="" means nothing selected; down should land on first item.
        result, changed = _apply_seq("\x1b[B", highlight=True, visible_ids=self.IDS, sel_id="")
        assert result == "a"
        assert changed is True

    def test_down_enables_highlight_and_selects_first_row_when_highlight_off(self):
        result, highlight, changed = _apply_seq_with_highlight(
            "\x1b[B",
            highlight=False,
            visible_ids=self.IDS,
            sel_id="",
        )
        assert result == "a"
        assert highlight is True
        assert changed is True

    def test_up_enables_highlight_and_selects_first_row_when_highlight_off(self):
        result, highlight, changed = _apply_seq_with_highlight(
            "\x1b[A",
            highlight=False,
            visible_ids=self.IDS,
            sel_id="",
        )
        assert result == "a"
        assert highlight is True
        assert changed is True

    def test_arrow_keeps_existing_selection_when_enabling_highlight(self):
        result, highlight, changed = _apply_seq_with_highlight(
            "\x1b[A",
            highlight=False,
            visible_ids=self.IDS,
            sel_id="b",
        )
        assert result == "b"
        assert highlight is True
        assert changed is True

    def test_arrow_enables_highlight_with_no_visible_rows(self):
        result, highlight, changed = _apply_seq_with_highlight(
            "\x1b[B",
            highlight=False,
            visible_ids=[],
            sel_id="",
        )
        assert result == ""
        assert highlight is True
        assert changed is True

    def test_arrow_with_empty_visible_ids_no_change(self):
        result, changed = _apply_seq("\x1b[A", highlight=True, visible_ids=[], sel_id="")
        assert result == ""
        assert changed is False


class TestBareEsc:
    def test_bare_esc_clears_selection(self):
        result, changed = _apply_seq("\x1b", highlight=True, visible_ids=["a", "b"], sel_id="a")
        assert result == ""
        assert changed is True

    def test_bare_esc_with_no_selection_stays_empty(self):
        result, changed = _apply_seq("\x1b", highlight=False, visible_ids=[], sel_id="")
        assert result == ""
        assert changed is False


class TestUnrecognisedSequences:
    IDS: ClassVar[list[str]] = ["a", "b"]

    def test_page_up_sequence_is_ignored(self):
        # \x1b[5~ - page up; should not crash or change sel_id.
        result, changed = _apply_seq("\x1b[5~", highlight=True, visible_ids=self.IDS, sel_id="b")
        assert result == "b"
        assert changed is False

    def test_f1_sequence_is_ignored(self):
        result, changed = _apply_seq("\x1bOP", highlight=True, visible_ids=self.IDS, sel_id="a")
        assert result == "a"
        assert changed is False

    def test_right_arrow_is_ignored(self):
        result, changed = _apply_seq("\x1b[C", highlight=True, visible_ids=self.IDS, sel_id="a")
        assert result == "a"
        assert changed is False

    def test_left_arrow_is_ignored(self):
        result, changed = _apply_seq("\x1b[D", highlight=True, visible_ids=self.IDS, sel_id="a")
        assert result == "a"
        assert changed is False


class TestCoalescedEscapeSequences:
    """Key repeat coalesces presses into one read buffer (F-404)."""

    IDS: ClassVar[list[str]] = ["a", "b", "c"]

    def test_split_separates_repeated_arrows(self) -> None:
        assert loops._split_escape_sequences("\x1b[B\x1b[B") == ["\x1b[B", "\x1b[B"]

    def test_split_bare_esc_falls_back_to_whole_buffer(self) -> None:
        assert loops._split_escape_sequences("\x1b") == ["\x1b"]

    def test_held_down_arrow_moves_two_rows(self) -> None:
        state.S.highlight = True
        state.S.visible_ids = self.IDS
        state.S.sel_id = "a"

        changed = False
        for one in loops._split_escape_sequences("\x1b[B\x1b[B"):
            changed = loops._apply_escape_sequence(one) or changed

        assert state.S.sel_id == "c"
        assert changed is True

    def test_held_up_arrow_moves_two_rows(self) -> None:
        state.S.highlight = True
        state.S.visible_ids = self.IDS
        state.S.sel_id = "c"

        for one in loops._split_escape_sequences("\x1b[A\x1b[A"):
            loops._apply_escape_sequence(one)

        assert state.S.sel_id == "a"


def test_utf8_decoder_buffers_split_multibyte_input():
    from codecs import getincrementaldecoder

    decoder = getincrementaldecoder("utf-8")(errors="replace")
    first, second = "ä".encode()

    assert decoder.decode(bytes([first])) == ""
    assert decoder.decode(bytes([second])) == "ä"


class TestCommandKeyNormalization:
    def test_greek_keyboard_position_letters_normalize_to_hotkeys(self) -> None:
        assert loops._normalize_command_key("ψ") == "c"
        assert loops._normalize_command_key("μ") == "m"
        assert loops._normalize_command_key("ν") == "n"
        assert loops._normalize_command_key("ρ") == "r"
        assert loops._normalize_command_key("ι") == "i"
        assert loops._normalize_command_key("η") == "h"
        assert loops._normalize_command_key("β") == "b"
        assert loops._normalize_command_key("λ") == "l"

    def test_cyrillic_keyboard_position_letters_normalize_to_hotkeys(self) -> None:
        assert loops._normalize_command_key("с") == "c"
        assert loops._normalize_command_key("ь") == "m"
        assert loops._normalize_command_key("т") == "n"
        assert loops._normalize_command_key("к") == "r"
        assert loops._normalize_command_key("ш") == "i"
        assert loops._normalize_command_key("р") == "h"
        assert loops._normalize_command_key("и") == "b"
        assert loops._normalize_command_key("д") == "l"
        assert loops._normalize_command_key("й") == "q"
        assert loops._normalize_command_key(".") == "/"

    def test_greek_keyboard_position_q_quits_from_semicolon_key(self) -> None:
        assert loops._normalize_command_key(";") == "q"

    def test_filter_typing_keeps_original_language_character(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = ""
        state.S.filter_cursor = 0

        loops._apply_filter_key("ψ")

        assert state.S.filter_str == "ψ"


def test_request_quit_wakes_waiting_draw_loop() -> None:
    assert not state.S.quit.is_set()
    assert not state.S.dirty.is_set()

    state.request_quit()

    assert state.S.quit.is_set()
    assert state.S.dirty.is_set()


def test_request_redraw_flags_resize_and_wakes_draw_loop() -> None:
    assert not state.S.resized.is_set()
    assert not state.S.dirty.is_set()

    state.request_redraw()

    assert state.S.resized.is_set()
    assert state.S.dirty.is_set()


def test_draw_loop_clears_screen_and_repaints_on_resize(monkeypatch) -> None:
    # F-203: when the resize flag is set, the draw loop must abandon its cached
    # diff state and repaint from a cleared screen instead of cursor-up diffing
    # against a layout that has rewrapped.
    import threading

    chunks: list[bytes] = []
    flushed = threading.Event()

    class _FakeBuffer:
        def write(self, data: bytes) -> int:
            chunks.append(data)
            return len(data)

        def flush(self) -> None:
            flushed.set()

    class _FakeStdout:
        buffer = _FakeBuffer()

    monkeypatch.setattr(loops.sys, "stdout", _FakeStdout())

    state.request_redraw()

    worker = threading.Thread(target=loops.draw_loop, daemon=True)
    worker.start()
    try:
        assert flushed.wait(2.0), "draw_loop never produced a frame"
    finally:
        state.request_quit()
        worker.join(timeout=2.0)

    assert b"\x1b[2J\x1b[H" in b"".join(chunks)


def test_keyboard_loop_uses_half_second_idle_poll_interval(monkeypatch) -> None:
    class _FakeStdin:
        def fileno(self) -> int:
            return 123

    timeouts: list[float | None] = []
    restored: list[tuple[int, object, object]] = []

    def fake_select(readers, writers, errors, timeout):
        timeouts.append(timeout)
        state.request_quit()
        return [], [], []

    monkeypatch.setattr(loops.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(loops.termios, "tcgetattr", lambda fd: "old")
    monkeypatch.setattr(
        loops.termios,
        "tcsetattr",
        lambda fd, when, old: restored.append((fd, when, old)),
    )
    monkeypatch.setattr(loops.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(loops.select, "select", fake_select)

    loops.keyboard_loop()

    assert timeouts == [0.5]
    assert restored == [(123, loops.termios.TCSADRAIN, "old")]


def test_keyboard_loop_quits_on_stdin_eof(monkeypatch) -> None:
    # F-506: after a TTY hangup select() reports the fd readable forever and
    # os.read returns b"". The loop must request quit and exit, not spin.
    class _FakeStdin:
        def fileno(self) -> int:
            return 123

    reads = 0

    def fake_read(fd, n):
        nonlocal reads
        reads += 1
        return b""  # EOF

    monkeypatch.setattr(loops.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(loops.termios, "tcgetattr", lambda fd: "old")
    monkeypatch.setattr(loops.termios, "tcsetattr", lambda fd, when, old: None)
    monkeypatch.setattr(loops.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(loops.select, "select", lambda r, w, e, t: (r, [], []))
    monkeypatch.setattr(loops.os, "read", fake_read)

    state.S.quit.clear()
    loops.keyboard_loop()  # returns instead of spinning

    assert state.S.quit.is_set()
    assert reads == 1  # EOF detected on the first read, no busy-spin


def test_enter_opens_detail_for_selected_visible_row() -> None:
    state.S.highlight = True
    state.S.visible_ids = ["a", "b"]
    state.S.sel_id = "b"

    changed = loops._apply_enter_key()

    assert changed is True
    assert state.S.detail_view is True


def test_enter_opening_detail_invalidates_selected_details_cache() -> None:
    state.S.highlight = True
    state.S.visible_ids = ["a", "b"]
    state.S.sel_id = "b"
    state.S.details = {
        "a": state.ContainerDetails(id="a", status="running"),
        "b": state.ContainerDetails(id="b", status="paused"),
    }

    changed = loops._apply_enter_key()

    assert changed is True
    assert state.S.detail_view is True
    assert state.S.details == {
        "a": state.ContainerDetails(id="a", status="running"),
    }


def test_enter_closes_detail_view_like_esc() -> None:
    state.S.highlight = True
    state.S.visible_ids = ["a"]
    state.S.sel_id = "a"
    state.S.detail_view = True
    state.S.details = {
        "a": state.ContainerDetails(id="a", status="running"),
    }

    changed = loops._apply_enter_key()

    assert changed is True
    assert state.S.detail_view is False
    assert state.S.sel_id == "a"
    assert state.S.details == {
        "a": state.ContainerDetails(id="a", status="running"),
    }


def test_enter_without_selection_opens_detail_for_first_visible_row() -> None:
    state.S.highlight = False
    state.S.visible_ids = ["a", "b"]
    state.S.sel_id = ""

    changed = loops._apply_enter_key()

    assert changed is True
    assert state.S.detail_view is True
    assert state.S.sel_id == "a"


def test_esc_closes_detail_view_before_clearing_selection() -> None:
    state.S.highlight = True
    state.S.visible_ids = ["a"]
    state.S.sel_id = "a"
    state.S.detail_view = True

    changed = loops._apply_escape_sequence("\x1b")

    assert changed is True
    assert state.S.detail_view is False
    assert state.S.sel_id == "a"


class TestFilterTyping:
    # F-403: while entering a filter, every printable key - including q/Q -
    # extends the filter string. Only Ctrl-C quits during filter typing.

    def test_q_extends_filter_without_quitting(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabbit"
        state.S.filter_cursor = len(state.S.filter_str)

        loops._apply_filter_key("q")

        assert state.S.filter_str == "rabbitq"
        assert state.S.filter_typing is True
        assert not state.S.quit.is_set()

    def test_uppercase_q_extends_filter_without_quitting(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "RABBIT"
        state.S.filter_cursor = len(state.S.filter_str)

        loops._apply_filter_key("Q")

        assert state.S.filter_str == "RABBITQ"
        assert not state.S.quit.is_set()

    def test_ctrl_c_still_quits(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabbit"

        loops._apply_filter_key("\x03")

        assert state.S.quit.is_set()
        assert state.S.filter_str == "rabbit"

    def test_enter_confirms_filter(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabbitmq"

        loops._apply_filter_key("\r")

        assert state.S.filter_typing is False
        assert state.S.filter_str == "rabbitmq"

    def test_esc_cancels_and_clears_filter(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabbitmq"

        loops._apply_filter_key("\x1b")

        assert state.S.filter_typing is False
        assert state.S.filter_str == ""

    def test_backspace_removes_last_char(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabbitq"
        state.S.filter_cursor = len(state.S.filter_str)

        loops._apply_filter_key("\x7f")

        assert state.S.filter_str == "rabbit"

    def test_left_arrow_keeps_filter_mode_and_moves_cursor_left(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabbit"
        state.S.filter_cursor = len(state.S.filter_str)

        loops._apply_filter_key("\x1b[D")

        assert state.S.filter_typing is True
        assert state.S.filter_str == "rabbit"
        assert state.S.filter_cursor == 5

    def test_right_arrow_keeps_filter_mode_and_moves_cursor_right(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabbit"
        state.S.filter_cursor = 3

        loops._apply_filter_key("\x1b[C")

        assert state.S.filter_typing is True
        assert state.S.filter_str == "rabbit"
        assert state.S.filter_cursor == 4

    def test_printable_key_inserts_at_filter_cursor(self) -> None:
        state.S.filter_typing = True
        state.S.filter_str = "rabit"
        state.S.filter_cursor = 2

        loops._apply_filter_key("b")

        assert state.S.filter_str == "rabbit"
        assert state.S.filter_cursor == 3


def test_next_bar_mode_hint_is_one_way():
    # First b press dismisses the hint (one-way); subsequent b presses toggle full <-> off.
    assert loops._next_bar_mode(state.BAR_HINT) == state.BAR_FULL
    assert loops._next_bar_mode(state.BAR_FULL) == state.BAR_OFF
    assert loops._next_bar_mode(state.BAR_OFF) == state.BAR_FULL
