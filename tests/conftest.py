import pytest

from dsw import ansi, state


@pytest.fixture(autouse=True)
def reset_global_state():
    state.reset()
    state.S.dirty.clear()
    ansi._no_color = True

    yield

    state.reset()
    ansi._no_color = False
