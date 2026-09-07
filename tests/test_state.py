import dataclasses
import threading

import pytest

from dsw import state
from dsw.state import Row


def make_row(cid: str, name: str) -> Row:
    return Row(
        id=cid,
        name=name,
        cpu_raw="1%",
        cpu_val=1.0,
        mem_usage="1MiB / 1GiB",
        mem_raw="1%",
        mem_val=1.0,
        net_io="0B / 0B",
        block_io="0B / 0B",
    )


def test_runtime_state_is_dataclass_with_lock() -> None:
    assert dataclasses.is_dataclass(state.S)
    assert isinstance(state.S, state.RuntimeState)
    assert isinstance(state.S.lock, type(threading.Lock()))


def test_snapshot_is_immutable_and_copies_mutable_fields() -> None:
    state.S.rows = [make_row("aa", "alpha")]

    snapshot = state.snapshot()
    state.S.rows.append(make_row("bb", "beta"))

    assert snapshot.rows == (make_row("aa", "alpha"),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.error = "changed"


def test_reset_restores_state_fields() -> None:
    state.S.rows = [make_row("aa", "alpha")]
    state.S.error = "boom"
    state.S.filter_str = "alpha"
    state.S.visible_ids = ["aa"]
    state.S.quit.set()
    state.S.dirty.clear()

    state.reset()

    assert state.S.rows == []
    assert state.S.error == ""
    assert state.S.filter_str == ""
    assert state.S.visible_ids == []
    assert not state.S.quit.is_set()
    assert state.S.dirty.is_set()


def test_reset_restores_every_data_field_to_its_default() -> None:
    # Dirty every non-Event/Lock field with a sentinel, then assert reset()
    # restores each to its dataclass default. This catches a future field
    # added to RuntimeState but forgotten in reset().
    event_fields = ("lock", "quit", "dirty", "resized")
    data_fields = [f for f in dataclasses.fields(state.S) if f.name not in event_fields]

    for f in data_fields:
        setattr(state.S, f.name, "DIRTY")

    state.reset()

    for f in data_fields:
        expected = f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default
        assert getattr(state.S, f.name) == expected, f.name
