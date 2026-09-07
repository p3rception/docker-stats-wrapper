import io
import itertools
import json
import os
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import cast

from dsw import data, state
from dsw.state import Row


def make_row(cid: str, cpu: float = 1.0, mem: float = 1.0) -> Row:
    return Row(
        id=cid,
        name="web",
        cpu_raw=f"{cpu:.0f}%",
        cpu_val=cpu,
        mem_usage="1MiB / 1GiB",
        mem_raw=f"{mem:.0f}%",
        mem_val=mem,
        net_io="0B / 0B",
        block_io="0B / 0B",
    )


class PipeProc:
    """Fake Popen whose stdout is a real pipe.

    DockerSource reads the stats stream with select() on the underlying file
    descriptor, so a StringIO stand-in is not enough. With keep_open=True the
    write end stays open and the stream goes quiet after `text` instead of
    hitting EOF.
    """

    def __init__(self, text: str = "", *, keep_open: bool = False) -> None:
        read_fd, write_fd = os.pipe()
        if text:
            os.write(write_fd, text.encode())
        self._write_fd: int | None = write_fd
        if not keep_open:
            self.close_write_end()
        self.stdout = os.fdopen(read_fd, "r")
        self.stderr = io.StringIO("")
        self.returncode = 0

    def close_write_end(self) -> None:
        if self._write_fd is not None:
            os.close(self._write_fd)
            self._write_fd = None

    def close(self) -> None:
        self.close_write_end()
        if not self.stdout.closed:
            self.stdout.close()

    def poll(self):
        return self.returncode

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def test_parse_float_rejects_non_finite_and_malformed_values():
    assert data._parse_float("") == 0.0
    assert data._parse_float("NaN%") == 0.0
    assert data._parse_float("inf%") == 0.0
    assert data._parse_float("12.5%") == 12.5


def test_parse_json_line_defaults_malformed_metric_fields():
    row = data._parse_json_line(
        json.dumps(
            {
                "ID": "abcdef1234567890",
                "Name": "web",
                "CPUPerc": None,
                "MemUsage": None,
                "MemPerc": None,
                "NetIO": {},
                "BlockIO": [],
            }
        )
    )

    assert row is not None
    assert row.id == "abcdef123456"
    assert row.name == "web"
    assert row.cpu_raw == "0%"
    assert row.cpu_val == 0.0
    assert row.mem_usage == ""
    assert row.mem_raw == "0%"
    assert row.mem_val == 0.0
    assert row.net_io == ""
    assert row.block_io == ""


def test_publish_rows_keeps_history_to_sparkline_width() -> None:
    state.S.history = {
        "web": tuple((float(i), float(i)) for i in range(28)),
    }

    data._publish_rows([make_row("web", cpu=28.0, mem=28.0)])

    assert len(state.S.history["web"]) == 28
    assert state.S.history["web"][0] == (1.0, 1.0)
    assert state.S.history["web"][-1] == (28.0, 28.0)


def test_refresh_details_skips_inspect_when_detail_view_closed(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(data, "_inspect_details", lambda runtime, ids: calls.append(ids) or {})
    state.S.detail_view = False
    state.S.sel_id = "abc123def456"

    data._refresh_details(data.DockerSource(), [make_row("abc123def456")])

    assert calls == []
    assert state.S.details == {}


def test_refresh_details_inspects_only_selected_container(monkeypatch):
    calls: list[list[str]] = []

    def fake_inspect(runtime, ids):
        calls.append(ids)
        return {cid: data.ContainerDetails(id=cid, image="img") for cid in ids}

    monkeypatch.setattr(data, "_inspect_details", fake_inspect)
    state.S.detail_view = True
    state.S.sel_id = "abc123def456"
    rows = [make_row("abc123def456"), make_row("fed654cba321")]

    data._refresh_details(data.DockerSource(), rows)

    assert calls == [["abc123def456"]]
    assert set(state.S.details) == {"abc123def456"}


def test_refresh_details_uses_cache_instead_of_reinspecting(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(data, "_inspect_details", lambda runtime, ids: calls.append(ids) or {})
    state.S.detail_view = True
    state.S.sel_id = "abc123def456"
    state.S.details = {"abc123def456": data.ContainerDetails(id="abc123def456")}

    data._refresh_details(data.DockerSource(), [make_row("abc123def456")])

    assert calls == []
    assert set(state.S.details) == {"abc123def456"}


def test_refresh_details_evicts_containers_absent_from_rows(monkeypatch):
    monkeypatch.setattr(data, "_inspect_details", lambda runtime, ids: {})
    state.S.detail_view = False
    state.S.details = {
        "abc123def456": data.ContainerDetails(id="abc123def456"),
        "fed654cba321": data.ContainerDetails(id="fed654cba321"),
    }

    data._refresh_details(data.DockerSource(), [make_row("abc123def456")])

    assert set(state.S.details) == {"abc123def456"}


def test_parse_json_line_sanitizes_json_escaped_terminal_controls() -> None:
    row = data._parse_json_line(
        json.dumps(
            {
                "ID": "abcdef1234567890",
                "Name": "\x1b[31mweb\x1b[0m\nnext",
                "CPUPerc": "\x1b[32m12.5%\x1b[0m",
                "MemUsage": "64MiB / 1GiB\x1b]52;c;payload\x07",
                "MemPerc": "6.25%",
                "NetIO": "0B / 0B\rhidden",
                "BlockIO": "0B / 0B",
            }
        )
    )

    assert row is not None
    assert "\x1b" not in row.name
    assert "\n" not in row.name
    assert row.name == "webnext"
    assert row.cpu_raw == "12.5%"
    assert row.cpu_val == 12.5
    assert "\x1b" not in row.mem_usage
    assert "\x07" not in row.mem_usage
    # The OSC payload text itself is stripped, not just the control bytes.
    assert "payload" not in row.mem_usage
    assert row.mem_usage == "64MiB / 1GiB"
    assert "\r" not in row.net_io


def test_inspect_details_terminates_argument_list(monkeypatch) -> None:
    # A container ID starting with '-' must reach inspect as an operand, not a flag.
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    data._inspect_details("docker", ["-rf"])
    assert seen == [["docker", "inspect", "--", "-rf"]]


def test_parse_inspect_output_sanitizes_detail_fields() -> None:
    # Whoever starts a container picks the bind-mount path, network name, and
    # port bindings, so inspect metadata is as untrusted as the stats rows.
    esc = "\x1b[2J"
    details = data._parse_inspect_output(
        json.dumps(
            [
                {
                    "Id": "abcdef123456abcdef",
                    "Mounts": [{"Source": f"\x1b]0;pwned\x07/tmp/x{esc}", "Destination": f"/data{esc}"}],
                    "NetworkSettings": {
                        "Ports": {f"80/tcp{esc}": [{"HostIp": f"0.0.0.0{esc}", "HostPort": f"8080{esc}"}]},
                        "Networks": {f"bri{esc}dge": {"IPAddress": f"172.17.0.2{esc}"}},
                    },
                }
            ]
        )
    )["abcdef123456"]

    assert details.mounts == "/tmp/x->/data"
    assert details.ports == "0.0.0.0:8080->80/tcp"
    assert details.network == "bridge"
    assert details.ip_address == "172.17.0.2"


def test_parse_json_line_strips_osc_payload_from_name() -> None:
    # OSC 52 (clipboard) carries a payload terminated by BEL or ST; the whole
    # sequence including the payload must be removed, not just the ESC/BEL bytes.
    row = data._parse_json_line(
        json.dumps({"ID": "abc", "Name": "\x1b]52;c;cGF5bG9hZA==\x07web"})
    )
    assert row is not None
    assert row.name == "web"

    row_st = data._parse_json_line(
        json.dumps({"ID": "abc", "Name": "safe\x1b]0;evil title\x1b\\name"})
    )
    assert row_st is not None
    assert row_st.name == "safename"


def test_parse_json_line_rejects_non_string_identity_fields():
    assert data._parse_json_line(json.dumps({"ID": None, "Name": "web"})) is None
    assert data._parse_json_line(json.dumps({"ID": "abcdef", "Name": None})) is None


def test_parse_json_line_accepts_podman_field_names():
    row = data._parse_json_line(
        json.dumps(
            {
                "id": "abcdef1234567890",
                "name": "web",
                "cpu_percent": "12.50%",
                "mem_usage": "64MiB / 1GiB",
                "mem_percent": "6.25%",
                "net_input": "1.2kB",
                "net_output": "3.4kB",
                "block_input": "5.6MB",
                "block_output": "7.8MB",
            }
        )
    )

    assert row is not None
    assert row.id == "abcdef123456"
    assert row.name == "web"
    assert row.cpu_raw == "12.50%"
    assert row.cpu_val == 12.5
    assert row.mem_usage == "64MiB / 1GiB"
    assert row.mem_raw == "6.25%"
    assert row.mem_val == 6.25
    assert row.net_io == "1.2kB / 3.4kB"
    assert row.block_io == "5.6MB / 7.8MB"


def test_stderr_drain_reads_stream_until_eof():
    buffer, thread = data._start_stderr_drain(io.StringIO("first\nsecond\n"))

    assert thread is not None
    thread.join(timeout=1)
    assert isinstance(buffer, data._StderrBuffer)
    assert buffer.text() == "first\nsecond"


def test_stop_process_terminates_before_waiting():
    class FakeProcess:
        returncode = None
        stdout = stderr = None

        def __init__(self):
            self.events: list[str] = []

        def poll(self):
            return self.returncode

        def terminate(self):
            self.events.append("terminate")
            self.returncode = 0

        def wait(self, timeout):
            self.events.append(f"wait:{timeout}")
            return 0

        def kill(self):
            self.events.append("kill")

    proc = FakeProcess()

    data._stop_process(proc, None)

    assert proc.events == ["terminate", "wait:2"]


def test_stop_process_kills_after_timeout():
    class HangingProcess:
        returncode = None
        stdout = stderr = None

        def __init__(self):
            self.events: list[str] = []
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.events.append("terminate")

        def wait(self, timeout):
            self.events.append(f"wait:{timeout}")
            if not self.killed:
                raise subprocess.TimeoutExpired("docker", timeout)
            self.returncode = -9
            return -9

        def kill(self):
            self.events.append("kill")
            self.killed = True

    proc = HangingProcess()

    data._stop_process(proc, None)

    assert proc.events == ["terminate", "wait:2", "kill", "wait:2"]


def test_stop_active_processes_terminates_registered_children() -> None:
    class FakeProcess:
        returncode = None
        stdout = stderr = None

        def __init__(self) -> None:
            self.events: list[str] = []

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.events.append("terminate")
            self.returncode = 0

        def wait(self, timeout: float | None) -> int:
            self.events.append(f"wait:{timeout}")
            return 0

        def kill(self) -> None:
            self.events.append("kill")

    proc = FakeProcess()
    data._register_process(cast("subprocess.Popen[str]", proc))

    data.stop_active_processes()
    data.stop_active_processes()

    assert proc.events == ["terminate", "wait:2"]


# -- inactivity batch flush ---------------------------------------------------

def test_partial_batch_flushes_after_inactivity_for_single_container(monkeypatch):
    """A single-container batch must be published via the inactivity flush.

    Simulate a docker stats stream that emits exactly one line (one container)
    and then goes quiet without closing.  fetcher_loop should still push the
    row to state.S.rows once _BATCH_FLUSH_TIMEOUT elapses rather than waiting
    for a duplicate ID that will never arrive.
    """
    row_line = json.dumps({
        "ID": "abcdef123456",
        "Name": "only_one",
        "CPUPerc": "2.50%",
        "MemUsage": "64MiB / 1GiB",
        "MemPerc": "6.25%",
        "NetIO": "0B / 0B",
        "BlockIO": "0B / 0B",
    })

    # Shorten the timeouts so the test is fast.
    monkeypatch.setattr(data, "_BATCH_FLUSH_TIMEOUT", 0.2)
    monkeypatch.setattr(data, "_STREAM_POLL_INTERVAL", 0.05)
    state.S.quit.clear()
    state.S.rows = []

    proc = PipeProc(row_line + "\n", keep_open=True)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)

    rows = []
    try:
        t = threading.Thread(target=data.fetcher_loop, daemon=True)
        t.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with state.S.lock:
                rows = list(state.S.rows)
            if rows:
                break
            time.sleep(0.02)
        state.S.quit.set()
        t.join(timeout=2)
    finally:
        proc.close()

    assert rows, "state.S.rows was never populated via the inactivity flush"
    assert rows[0].name == "only_one"


def test_fetcher_loop_publishes_cycle_without_threading_timer(monkeypatch):
    rows_json = [
        {
            "ID": "web123456789",
            "Name": "web",
            "CPUPerc": "1.00%",
            "MemUsage": "64MiB / 1GiB",
            "MemPerc": "6.25%",
            "NetIO": "0B / 0B",
            "BlockIO": "0B / 0B",
        },
        {
            "ID": "db1234567890",
            "Name": "db",
            "CPUPerc": "2.00%",
            "MemUsage": "128MiB / 1GiB",
            "MemPerc": "12.50%",
            "NetIO": "0B / 0B",
            "BlockIO": "0B / 0B",
        },
        {
            "ID": "web123456789",
            "Name": "web",
            "CPUPerc": "3.00%",
            "MemUsage": "96MiB / 1GiB",
            "MemPerc": "9.38%",
            "NetIO": "0B / 0B",
            "BlockIO": "0B / 0B",
        },
    ]
    proc = PipeProc("\n".join(json.dumps(row) for row in rows_json) + "\n", keep_open=True)

    def forbidden_timer(*args, **kwargs):
        raise AssertionError("fetcher_loop should not create per-line threading.Timer instances")

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr(data.threading, "Timer", forbidden_timer, raising=False)
    state.S.quit.clear()

    rows = []
    try:
        t = threading.Thread(target=data.fetcher_loop, daemon=True)
        t.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with state.S.lock:
                rows = list(state.S.rows)
            if [row.name for row in rows] == ["web", "db"]:
                break
            time.sleep(0.05)
        state.S.quit.set()
        t.join(timeout=2)
    finally:
        proc.close()

    assert [row.name for row in rows] == ["web", "db"]


def test_fetcher_loop_clears_stale_rows_when_process_has_no_rows(monkeypatch):
    state.S.rows = [
        state.Row(
            id="stale",
            name="stale",
            cpu_raw="1%",
            cpu_val=1.0,
            mem_usage="1MiB / 1GiB",
            mem_raw="1%",
            mem_val=1.0,
            net_io="0B / 0B",
            block_io="0B / 0B",
        ),
    ]
    state.S.quit.clear()

    proc = PipeProc("")

    def stop_after_first_wait(timeout=None):
        state.S.quit.set()
        return True

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr(state.S.quit, "wait", stop_after_first_wait)

    try:
        data.fetcher_loop()
    finally:
        proc.close()

    assert state.S.rows == []


def test_fetcher_loop_clears_stale_rows_when_stream_goes_quiet(monkeypatch):
    monkeypatch.setattr(data, "_STALE_ROWS_TIMEOUT", 0.1)
    monkeypatch.setattr(data, "_STREAM_POLL_INTERVAL", 0.05)
    state.S.rows = [
        state.Row(
            id="stale",
            name="stale",
            cpu_raw="1%",
            cpu_val=1.0,
            mem_usage="1MiB / 1GiB",
            mem_raw="1%",
            mem_val=1.0,
            net_io="0B / 0B",
            block_io="0B / 0B",
        ),
    ]
    state.S.quit.clear()

    # The write end stays open: the stream never produces output and never
    # reaches EOF, exactly like a daemon that has gone quiet.
    proc = PipeProc("", keep_open=True)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)

    rows = []
    error = ""
    try:
        t = threading.Thread(target=data.fetcher_loop, daemon=True)
        t.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with state.S.lock:
                rows = list(state.S.rows)
                error = state.S.error
            if not rows:
                break
            time.sleep(0.02)
        state.S.quit.set()
        t.join(timeout=2)
    finally:
        proc.close()

    assert rows == []
    # A quiet daemon publishes a notice, not the empty-host state, so the UI
    # does not read as "no containers running" during a stall.
    assert "no stats received" in error


def test_fetcher_loop_selects_first_row_when_highlight_starts_enabled(monkeypatch):
    row_json = {
        "ID": "abcdef123456",
        "Name": "only_one",
        "CPUPerc": "2.50%",
        "MemUsage": "64MiB / 1GiB",
        "MemPerc": "6.25%",
        "NetIO": "0B / 0B",
        "BlockIO": "0B / 0B",
    }
    state.S.highlight = True
    state.S.quit.clear()

    proc = PipeProc(json.dumps(row_json) + "\n")

    def stop_after_first_wait(timeout=None):
        state.S.quit.set()
        return True

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr(state.S.quit, "wait", stop_after_first_wait)

    try:
        data.fetcher_loop()
    finally:
        proc.close()

    assert state.S.sel_id == "abcdef123456"


def test_docker_source_yields_rows_from_stats_command(monkeypatch):
    row_json = {
        "ID": "abcdef1234567890",
        "Name": "web",
        "CPUPerc": "2.50%",
        "MemUsage": "64MiB / 1GiB",
        "MemPerc": "6.25%",
        "NetIO": "0B / 0B",
        "BlockIO": "0B / 0B",
    }
    commands = []
    proc = PipeProc(json.dumps(row_json) + "\n")

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    source = data.DockerSource()
    try:
        rows = [row for row in source if row is not None]
        source.close()
    finally:
        proc.close()

    assert commands == [["docker", "stats", "--format", "{{json .}}"]]
    assert [row.name for row in rows] == ["web"]


def test_docker_source_reassembles_lines_split_across_chunked_reads(monkeypatch):
    """Lines longer than one fd read arrive intact, including split UTF-8.

    The stream is read in 4 KiB chunks straight off the pipe fd, so a long
    line spans multiple reads and a multi-byte character can be cut at the
    chunk boundary.  Pad the name so the two-byte "é" sequence straddles
    byte 4096 exactly.
    """
    prefix = '{"ID": "abcdef1234567890", "Name": "'
    pad_len = 4095 - len(prefix.encode())
    name = "x" * pad_len + "é" * 50
    line = prefix + name + '"}'
    proc = PipeProc(line + "\n")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)

    source = data.DockerSource()
    try:
        rows = [row for row in source if row is not None]
        source.close()
    finally:
        proc.close()

    assert len(rows) == 1
    assert rows[0].name == name
    assert "�" not in rows[0].name


def test_docker_source_parses_final_line_without_trailing_newline(monkeypatch):
    row_json = {"ID": "abcdef1234567890", "Name": "web", "CPUPerc": "1.00%"}
    proc = PipeProc(json.dumps(row_json))  # no trailing newline before EOF
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)

    source = data.DockerSource()
    try:
        rows = [row for row in source if row is not None]
        source.close()
    finally:
        proc.close()

    assert [row.name for row in rows] == ["web"]


def test_podman_source_uses_podman_stats_command(monkeypatch):
    commands = []
    proc = PipeProc("")

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    source = data.DockerSource(runtime="podman")
    try:
        list(source)
        source.close()
    finally:
        proc.close()

    assert commands == [["podman", "stats", "--format", "{{json .}}"]]


def test_file_source_replays_json_lines(tmp_path):
    replay_path = tmp_path / "stats.jsonl"
    replay_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ID": "web1234567890",
                        "Name": "web",
                        "CPUPerc": "1.00%",
                        "MemUsage": "64MiB / 1GiB",
                        "MemPerc": "6.25%",
                        "NetIO": "0B / 0B",
                        "BlockIO": "0B / 0B",
                    }
                ),
                "not json",
                json.dumps(
                    {
                        "ID": "db1234567890",
                        "Name": "db",
                        "CPUPerc": "2.00%",
                        "MemUsage": "128MiB / 1GiB",
                        "MemPerc": "12.50%",
                        "NetIO": "0B / 0B",
                        "BlockIO": "0B / 0B",
                    }
                ),
            ]
        )
        + "\n"
    )

    rows = list(itertools.islice(data.FileSource(str(replay_path), frame_interval=0.0), 2))

    assert [row.name for row in rows] == ["web", "db"]


def test_file_source_loops_replay_rows(tmp_path):
    replay_path = tmp_path / "stats.jsonl"
    replay_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ID": "web1234567890",
                        "Name": "web",
                        "CPUPerc": "1.00%",
                        "MemUsage": "64MiB / 1GiB",
                        "MemPerc": "6.25%",
                        "NetIO": "0B / 0B",
                        "BlockIO": "0B / 0B",
                    }
                ),
                json.dumps(
                    {
                        "ID": "db1234567890",
                        "Name": "db",
                        "CPUPerc": "2.00%",
                        "MemUsage": "128MiB / 1GiB",
                        "MemPerc": "12.50%",
                        "NetIO": "0B / 0B",
                        "BlockIO": "0B / 0B",
                    }
                ),
            ]
        )
        + "\n"
    )

    rows = list(itertools.islice(data.FileSource(str(replay_path), frame_interval=0.0), 5))

    assert [row.name for row in rows] == ["web", "db", "web", "db", "web"]


def test_default_source_factory_uses_replay_file(tmp_path):
    replay_path = tmp_path / "stats.jsonl"
    replay_path.write_text(
        json.dumps(
            {
                "ID": "web1234567890",
                "Name": "web",
                "CPUPerc": "1.00%",
                "MemUsage": "64MiB / 1GiB",
                "MemPerc": "6.25%",
                "NetIO": "0B / 0B",
                "BlockIO": "0B / 0B",
            }
        )
        + "\n"
    )
    state.S.args = SimpleNamespace(replay=str(replay_path), runtime="docker")

    source = data._default_source_factory()

    assert isinstance(source, data.FileSource)
    assert [row.name for row in itertools.islice(source, 1)] == ["web"]


def test_fetcher_loop_does_not_report_restart_while_replay_file_loops(tmp_path, monkeypatch):
    replay_path = tmp_path / "stats.jsonl"
    replay_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ID": "web1234567890",
                        "Name": "web",
                        "CPUPerc": "1.00%",
                        "MemUsage": "64MiB / 1GiB",
                        "MemPerc": "6.25%",
                        "NetIO": "0B / 0B",
                        "BlockIO": "0B / 0B",
                    }
                ),
                json.dumps(
                    {
                        "ID": "web1234567890",
                        "Name": "web",
                        "CPUPerc": "2.00%",
                        "MemUsage": "96MiB / 1GiB",
                        "MemPerc": "9.38%",
                        "NetIO": "1kB / 2kB",
                        "BlockIO": "0B / 0B",
                    }
                ),
            ]
        )
        + "\n"
    )
    state.S.args = SimpleNamespace(replay=str(replay_path), runtime="docker")

    monkeypatch.setattr(data, "_REPLAY_FRAME_INTERVAL", 0.0)

    thread = threading.Thread(target=data.fetcher_loop, daemon=True)
    thread.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if state.S.rows:
            break
        time.sleep(0.02)
    state.request_quit()
    thread.join(timeout=2)

    assert state.S.error == ""
    assert [row.name for row in state.S.rows] == ["web"]


def test_fetcher_loop_can_consume_pluggable_stats_source(monkeypatch):
    rows = [
        state.Row("web123456789", "web", "1%", 1.0, "64MiB / 1GiB", "6.25%", 6.25, "0B / 0B", "0B / 0B"),
        state.Row("db1234567890", "db", "2%", 2.0, "64MiB / 1GiB", "6.25%", 6.25, "0B / 0B", "0B / 0B"),
        state.Row("web123456789", "web", "3%", 3.0, "64MiB / 1GiB", "6.25%", 6.25, "0B / 0B", "0B / 0B"),
    ]

    class FakeSource:
        stderr_text = ""

        def __iter__(self):
            yield from rows

        def close(self):
            state.S.quit.set()

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError))

    data.fetcher_loop(source_factory=FakeSource)

    assert [row.name for row in state.S.rows] == ["web", "db"]


# -- stderr drain O(1) truncation (F-8) --------------------------------------

def test_stderr_drain_truncates_to_limit():
    # Feed more data than _STDERR_LIMIT to verify it is capped.
    big_line = "x" * (data._STDERR_LIMIT + 500) + "\n"
    buffer, thread = data._start_stderr_drain(io.StringIO(big_line))

    assert thread is not None
    thread.join(timeout=1)
    assert len(buffer.text()) <= data._STDERR_LIMIT


def test_stderr_drain_running_total_stays_bounded():
    # Many small lines that together exceed the limit.
    content = ("err\n" * (data._STDERR_LIMIT // 4 + 10))
    buffer, thread = data._start_stderr_drain(io.StringIO(content))

    assert thread is not None
    thread.join(timeout=1)
    assert len(buffer.text()) <= data._STDERR_LIMIT


def test_stderr_buffer_concurrent_append_and_read_is_race_free():
    # F-505: a reader joins the buffer while the drain thread is still
    # appending/trimming. The shared lock must prevent "deque mutated during
    # iteration" and the buffer must end up holding the most recent output.
    buffer = data._StderrBuffer()
    n = 4000
    errors: list[Exception] = []

    def writer() -> None:
        for i in range(n):
            buffer.append(f"line{i}\n")

    def reader() -> None:
        try:
            for _ in range(n):
                buffer.text()
        except Exception as exc:  # the RuntimeError under test, if unguarded
            errors.append(exc)

    wt = threading.Thread(target=writer)
    rt = threading.Thread(target=reader)
    wt.start()
    rt.start()
    wt.join(timeout=5)
    rt.join(timeout=5)

    assert errors == []
    final = buffer.text()
    assert len(final) <= data._STDERR_LIMIT
    assert f"line{n - 1}" in final  # latest output retained after trimming


def test_fetcher_loop_stops_retrying_when_docker_cli_is_missing(monkeypatch):
    popen_calls = 0
    wait_args = []

    def fake_popen(*args, **kwargs):
        nonlocal popen_calls
        popen_calls += 1
        if popen_calls > 1:
            raise AssertionError("fetcher_loop retried after FileNotFoundError")
        raise FileNotFoundError

    def fake_wait(timeout=None):
        wait_args.append(timeout)
        return False

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(state.S.quit, "wait", fake_wait)

    data.fetcher_loop()

    assert popen_calls == 1
    assert wait_args == [None]


def test_fetcher_loop_reports_selected_runtime_when_cli_is_missing(monkeypatch):
    class MissingPodmanSource:
        runtime = "podman"
        stderr_text = ""

        def __iter__(self):
            raise FileNotFoundError

        def close(self) -> None:
            pass

    def fake_wait(timeout=None):
        return False

    monkeypatch.setattr(state.S.quit, "wait", fake_wait)

    data.fetcher_loop(source_factory=MissingPodmanSource)

    assert state.S.error == "podman CLI not found on PATH"


def test_fetcher_loop_reports_selected_runtime_when_stats_exits_without_stderr(monkeypatch):
    class EmptyPodmanSource:
        runtime = "podman"
        stderr_text = ""

        def __iter__(self):
            return iter(())

        def close(self) -> None:
            pass

    wait_args = []

    def fake_wait(timeout=None):
        wait_args.append(timeout)
        state.S.quit.set()
        return True

    monkeypatch.setattr(state.S.quit, "wait", fake_wait)

    data.fetcher_loop(source_factory=EmptyPodmanSource)

    assert state.S.error == "podman stats exited - restarting..."
    assert wait_args == [data._BACKOFF_INITIAL]
