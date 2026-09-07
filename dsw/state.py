"""
state.py - shared runtime state and data types.

All mutable state that threads read and write lives here.
No imports from other dsw modules - this is the base layer.
"""

import argparse
import datetime
import threading
from dataclasses import dataclass, field
from typing import NamedTuple

# -- configuration --------------------------------------------------------------

# Shared by both render.py and data.py (history ring buffer width), so it stays
# in this base layer. Pure render-layout constants live in render.py.
HISTORY_SPARKLINE_WIDTH = 28

# -- data types -----------------------------------------------------------------

class Row(NamedTuple):
    id:        str
    name:      str
    cpu_raw:   str
    cpu_val:   float
    mem_usage: str
    mem_raw:   str
    mem_val:   float
    net_io:    str
    block_io:  str
    pids:      str = ""


class ContainerDetails(NamedTuple):
    id:            str
    image:         str = ""
    status:        str = ""
    created:       str = ""
    restart_count: str = ""
    ports:         str = ""
    mounts:        str = ""
    network:       str = ""
    ip_address:    str = ""

# Key-bar display, cycled by `b`: a one-line hint -> the full key bar -> nothing.
BAR_HINT, BAR_FULL, BAR_OFF = 0, 1, 2

# -- shared state ---------------------------------------------------------------


@dataclass(frozen=True)
class StateSnapshot:
    rows: tuple[Row, ...]
    error: str
    sort_key: str
    sort_asc: bool
    last_ts: datetime.datetime | None
    show_id: bool
    bar_mode: int
    paused: bool
    paused_rows: tuple[Row, ...]
    paused_ts: datetime.datetime | None
    filter_str: str
    filter_typing: bool
    filter_cursor: int
    top_n: int
    highlight: bool
    sel_id: str
    detail_view: bool
    history: dict[str, tuple[tuple[float, float], ...]]
    details: dict[str, ContainerDetails]


@dataclass
class RuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    rows: list[Row] = field(default_factory=list)
    error: str = ""
    sort_key: str = "cpu"
    sort_asc: bool = False   # True = ascending, False = descending
    last_ts: datetime.datetime | None = None
    quit: threading.Event = field(default_factory=threading.Event)
    show_id: bool = False
    show_help: bool = False
    bar_mode: int = BAR_HINT  # hint shown once on start; b then toggles full <-> off
    paused: bool = False
    paused_rows: list[Row] = field(default_factory=list)  # frozen snapshot while paused
    paused_ts: datetime.datetime | None = None
    filter_str: str = ""     # active substring filter (empty = off)
    filter_typing: bool = False  # True while the user is typing a filter
    filter_cursor: int = 0   # cursor offset inside filter_str while typing
    top_n: int = 0      # 0 = show all; >0 = show only first N rows
    highlight: bool = False  # row highlight off by default; l turns it on
    sel_id: str = ""     # id of selected container; "" = none
    visible_ids: list[str] = field(default_factory=list)  # ids in current display order
    detail_view: bool = False
    history: dict[str, tuple[tuple[float, float], ...]] = field(default_factory=dict)
    details: dict[str, ContainerDetails] = field(default_factory=dict)
    dirty: threading.Event = field(default_factory=threading.Event)
    resized: threading.Event = field(default_factory=threading.Event)  # SIGWINCH pending
    args: argparse.Namespace | None = None

    def snapshot(self) -> StateSnapshot:
        with self.lock:
            return StateSnapshot(
                rows=tuple(self.rows),
                error=self.error,
                sort_key=self.sort_key,
                sort_asc=self.sort_asc,
                last_ts=self.last_ts,
                show_id=self.show_id,
                bar_mode=self.bar_mode,
                paused=self.paused,
                paused_rows=tuple(self.paused_rows),
                paused_ts=self.paused_ts,
                filter_str=self.filter_str,
                filter_typing=self.filter_typing,
                filter_cursor=self.filter_cursor,
                top_n=self.top_n,
                highlight=self.highlight,
                sel_id=self.sel_id,
                detail_view=self.detail_view,
                history=dict(self.history),
                details=dict(self.details),
            )

S = RuntimeState()
S.dirty.set()  # force first paint


def snapshot() -> StateSnapshot:
    return S.snapshot()


def reset() -> None:
    # Replace the singleton with a fresh instance: every field returns to its
    # dataclass default (so a newly added field can never leak between tests)
    # without re-listing or reflecting over them. No module captures `S` by
    # value - all access goes through `state.S` - so the rebind is safe.
    global S
    S = RuntimeState()
    S.dirty.set()


def request_quit() -> None:
    S.quit.set()
    S.dirty.set()


def request_redraw() -> None:
    """Flag a full repaint (e.g. on SIGWINCH) and wake the draw loop."""
    S.resized.set()
    S.dirty.set()
