"""
data.py - docker stats subprocess, line parsing, and the fetcher thread.
"""

import contextlib
import datetime
import json
import logging
import math
import os
import select
import subprocess
import threading
import time
import unicodedata
from codecs import getincrementaldecoder
from collections import deque
from collections.abc import Callable, Iterator
from typing import IO, Protocol

from . import state
from .ansi import _strip_ansi
from .state import HISTORY_SPARKLINE_WIDTH, ContainerDetails, Row

_log = logging.getLogger(__name__)

_BACKOFF_INITIAL = 2.0
_BACKOFF_MAX = 60.0
_STDERR_LIMIT = 4096
# Maximum time to wait for a second container line before flushing whatever
# has accumulated.  This ensures a single-container host still gets a live
# update each cycle rather than waiting for the *next* cycle to detect the
# duplicate-ID sentinel.
_BATCH_FLUSH_TIMEOUT = 1.5
_STALE_ROWS_TIMEOUT = 3.5
_REPLAY_FRAME_INTERVAL = 1.0
# How long the stats reader sleeps in select() when no output is arriving.
# Each timeout yields a heartbeat (None) so the consumer can apply the batch
# flush and stale-rows thresholds; both are measured against monotonic
# timestamps, so a coarse poll keeps them accurate to within half a second
# without needless wakeup churn.
_STREAM_POLL_INTERVAL = 0.5

_active_processes: set[subprocess.Popen[str]] = set()
_active_processes_lock = threading.Lock()


def _register_process(proc: subprocess.Popen[str]) -> None:
    with _active_processes_lock:
        _active_processes.add(proc)


def _unregister_process(proc: subprocess.Popen[str]) -> None:
    with _active_processes_lock:
        _active_processes.discard(proc)


def stop_active_processes() -> None:
    """Terminate any docker stats children still owned by the fetcher."""
    with _active_processes_lock:
        processes = list(_active_processes)

    for proc in processes:
        _stop_process(proc, None)


class StatsSource(Protocol):
    """Yields parsed rows, interleaved with ``None`` heartbeats while idle.

    Heartbeats hand control back to the consumer so it can enforce its
    time-based behavior (partial-batch flush, stale-row clearing) without
    auxiliary timer threads. Sources that pace themselves and cannot go
    silently quiet (e.g. FileSource) may yield rows only.
    """

    stderr_text: str

    def __iter__(self) -> Iterator[Row | None]:
        ...

    def close(self) -> None:
        ...


class DockerSource:
    def __init__(self, runtime: str = "docker") -> None:
        self.runtime = runtime
        self.proc: subprocess.Popen[str] | None = None
        self.stderr_buffer = _StderrBuffer()
        self.stderr_thread: threading.Thread | None = None
        self.stderr_text = ""

    def command(self) -> list[str]:
        return [
            self.runtime,
            "stats",
            "--format",
            "{{json .}}",
        ]

    def __iter__(self) -> Iterator[Row | None]:
        # The text wrapper is for the stderr drain; stdout is read at the fd
        # level in _read_stream and never through it. Decode with errors=replace
        # so invalid UTF-8 on stderr cannot raise in the drain thread and lose
        # the daemon error we need to show the user.
        self.proc = subprocess.Popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
        _register_process(self.proc)
        self.stderr_buffer, self.stderr_thread = _start_stderr_drain(self.proc.stderr)

        if self.proc.stdout is None:
            _log.error("%s stats stdout pipe is None; cannot read output", self.runtime)
            return

        yield from self._read_stream(self.proc.stdout.fileno())
        self.stderr_text = self.stderr_buffer.text()

    def _read_stream(self, fd: int) -> Iterator[Row | None]:
        """Read JSON lines from the stats pipe, yielding None while idle.

        select() with a timeout - instead of a blocking readline - keeps the
        reader responsive to quit and produces the heartbeats the consumer's
        flush/staleness deadlines depend on. Reads happen on the raw fd, so
        bytes are decoded incrementally and split into lines here: a chunk
        can end mid-line or mid-UTF-8-sequence.
        """
        decoder = getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while not state.S.quit.is_set():
            readable, _, _ = select.select([fd], [], [], _STREAM_POLL_INTERVAL)
            if not readable:
                yield None
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError as exc:
                _log.debug("error reading %s stats stdout: %s", self.runtime, exc)
                return
            if not chunk:
                # EOF: the stats process exited. A final line without a
                # trailing newline is still a line.
                tail = _strip_ansi(pending).rstrip()
                if tail:
                    row = _parse_json_line(tail)
                    if row is not None:
                        yield row
                return
            pending += decoder.decode(chunk)
            *complete, pending = pending.split("\n")
            yielded = False
            for raw_line in complete:
                line = _strip_ansi(raw_line).rstrip()
                if not line:
                    continue
                row = _parse_json_line(line)
                if row is None:
                    _log.debug("skipping malformed line: %r", line)
                    continue
                yielded = True
                yield row
            if not yielded:
                # Data arrived but contained no complete row (blank lines,
                # malformed output, or a partial line); still give the
                # consumer a chance to run its deadline checks.
                yield None

    def close(self) -> None:
        if self.proc is not None:
            _stop_process(self.proc, self.stderr_thread)
            self.proc = None
        self.stderr_text = self.stderr_buffer.text()


class FileSource:
    def __init__(
        self,
        path: str,
        *,
        frame_interval: float = _REPLAY_FRAME_INTERVAL,
    ) -> None:
        self.path = path
        self.stderr_text = ""
        self.frame_interval = frame_interval

    def __iter__(self) -> Iterator[Row]:
        seen_ids: set[str] = set()
        while not state.S.quit.is_set():
            emitted = False
            with open(self.path, encoding="utf-8") as stream:
                for line in stream:
                    if state.S.quit.is_set():
                        return
                    line = _strip_ansi(line).rstrip()
                    if not line:
                        continue
                    row = _parse_json_line(line)
                    if row is None:
                        _log.debug("skipping malformed replay line: %r", line)
                        continue
                    if row.id in seen_ids:
                        if self.frame_interval > 0 and state.S.quit.wait(self.frame_interval):
                            return
                        seen_ids.clear()
                    seen_ids.add(row.id)
                    emitted = True
                    yield row
            if not emitted:
                return
            if seen_ids and self.frame_interval > 0 and state.S.quit.wait(self.frame_interval):
                return
            seen_ids.clear()

    def close(self) -> None:
        # ponytail: replay opens the file per pass with `with`; nothing persists.
        pass


def _parse_float(s: str) -> float:
    try:
        value = float(s.strip().rstrip("%"))
    except ValueError:
        return 0.0
    return value if math.isfinite(value) else 0.0


def _optional_str_any(obj: dict[str, object], *keys: str, default: str = "") -> str:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str):
            return _sanitize_text(value)
        if isinstance(value, int | float) and math.isfinite(float(value)):
            return str(value)
    return default


def _optional_percent(obj: dict[str, object], *keys: str, default: str = "0%") -> str:
    value = _optional_str_any(obj, *keys, default=default)
    if value and value != default and not value.strip().endswith("%"):
        return f"{value}%"
    return value


def _optional_io_pair(
    obj: dict[str, object],
    combined_key: str,
    input_key: str,
    output_key: str,
) -> str:
    combined = _optional_str_any(obj, combined_key)
    if combined:
        return combined
    input_value = _optional_str_any(obj, input_key)
    output_value = _optional_str_any(obj, output_key)
    if input_value or output_value:
        return f"{input_value or '0B'} / {output_value or '0B'}"
    return ""


def _sanitize_text(s: str) -> str:
    """Remove terminal controls from docker-provided display fields."""
    stripped = _strip_ansi(s)
    return "".join(ch for ch in stripped if unicodedata.category(ch)[0] != "C")


def _parse_json_line(line: str) -> Row | None:
    """Parse a single JSON object from `docker stats --format '{{json .}}'`."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    cid_raw = obj.get("ID", obj.get("id", ""))
    name = obj.get("Name", obj.get("name", ""))
    if not isinstance(cid_raw, str) or not isinstance(name, str):
        return None

    cid = _sanitize_text(cid_raw)[:12]
    name = _sanitize_text(name)
    cpu_raw = _optional_percent(obj, "CPUPerc", "cpu_percent")
    mem_usage = _optional_str_any(obj, "MemUsage", "mem_usage")
    mem_raw = _optional_percent(obj, "MemPerc", "mem_percent")
    net_io = _optional_io_pair(obj, "NetIO", "net_input", "net_output")
    block_io = _optional_io_pair(obj, "BlockIO", "block_input", "block_output")
    pids = _optional_str_any(obj, "PIDs", "pids")

    return Row(
        id        = cid,
        name      = name,
        cpu_raw   = cpu_raw,
        cpu_val   = _parse_float(cpu_raw),
        mem_usage = mem_usage,
        mem_raw   = mem_raw,
        mem_val   = _parse_float(mem_raw),
        net_io    = net_io,
        block_io  = block_io,
        pids      = pids,
    )


def _join_strings(values: list[str], limit: int = 4) -> str:
    clean = [value for value in values if value]
    if not clean:
        return ""
    shown = clean[:limit]
    suffix = f", +{len(clean) - limit} more" if len(clean) > limit else ""
    return ", ".join(shown) + suffix


def _format_ports(network_settings: object) -> str:
    if not isinstance(network_settings, dict):
        return ""
    ports_obj = network_settings.get("Ports")
    if not isinstance(ports_obj, dict):
        return ""
    ports: list[str] = []
    for container_port_raw, bindings in ports_obj.items():
        if not isinstance(container_port_raw, str):
            continue
        container_port = _sanitize_text(container_port_raw)
        if isinstance(bindings, list) and bindings:
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                host_ip = binding.get("HostIp")
                host_port = binding.get("HostPort")
                host = _sanitize_text(":".join(
                    part for part in (str(host_ip) if host_ip else "", str(host_port) if host_port else "") if part
                ))
                ports.append(f"{host}->{container_port}" if host else container_port)
        else:
            ports.append(container_port)
    return _join_strings(ports)


def _format_mounts(mounts_obj: object) -> str:
    if not isinstance(mounts_obj, list):
        return ""
    mounts: list[str] = []
    for mount in mounts_obj:
        if not isinstance(mount, dict):
            continue
        source = mount.get("Source")
        destination = mount.get("Destination")
        if isinstance(source, str):
            source = _sanitize_text(source)
        if isinstance(destination, str):
            destination = _sanitize_text(destination)
        if isinstance(source, str) and isinstance(destination, str):
            mounts.append(f"{source}->{destination}")
        elif isinstance(destination, str):
            mounts.append(destination)
    return _join_strings(mounts)


def _format_networks(network_settings: object) -> tuple[str, str]:
    """Return (network_names, ip_addresses) from NetworkSettings."""
    if not isinstance(network_settings, dict):
        return "", ""
    networks = network_settings.get("Networks")
    if not isinstance(networks, dict):
        return "", ""
    names: list[str] = []
    ips: list[str] = []
    for name, net_obj in networks.items():
        if not isinstance(name, str):
            continue
        names.append(_sanitize_text(name))
        if isinstance(net_obj, dict):
            ip = net_obj.get("IPAddress")
            if isinstance(ip, str) and ip:
                ips.append(_sanitize_text(ip))
    return _join_strings(names), _join_strings(ips)


def _parse_inspect_output(output: str) -> dict[str, ContainerDetails]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list):
        return {}

    result: dict[str, ContainerDetails] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        cid = _optional_str_any(item, "Id")[:12]
        if not cid:
            continue
        config = item.get("Config")
        state_obj = item.get("State")
        image = _optional_str_any(item, "Image")
        if isinstance(config, dict):
            image = _optional_str_any(config, "Image", default=image)
        status = _optional_str_any(state_obj, "Status") if isinstance(state_obj, dict) else ""
        network_settings = item.get("NetworkSettings")
        network, ip_address = _format_networks(network_settings)
        result[cid] = ContainerDetails(
            id=cid,
            image=image,
            status=status,
            created=_optional_str_any(item, "Created"),
            restart_count=_optional_str_any(item, "RestartCount"),
            ports=_format_ports(network_settings),
            mounts=_format_mounts(item.get("Mounts")),
            network=network,
            ip_address=ip_address,
        )
    return result


def _inspect_details(runtime: str, ids: list[str]) -> dict[str, ContainerDetails]:
    if not ids:
        return {}
    try:
        result = subprocess.run(
            [runtime, "inspect", "--", *ids],
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("container inspect failed: %s", exc)
        return {}
    if result.returncode != 0:
        _log.debug("container inspect exited with %s: %s", result.returncode, result.stderr.strip())
        return {}
    return _parse_inspect_output(result.stdout)


class _StderrBuffer:
    """Thread-safe accumulator for the docker stderr drain.

    The drain thread appends and trims under the lock; readers fetch the
    joined text under the same lock via ``text()``. Without this shared lock,
    a reader joining the underlying deque while the drain thread is still
    writing (stderr output racing stdout EOF) could raise ``RuntimeError:
    deque mutated during iteration`` - which escapes ``fetcher_loop``'s
    narrower ``except`` tuple and silently kills the fetcher thread, freezing
    the UI on stale data.
    """

    def __init__(self) -> None:
        self._lines: deque[str] = deque()
        self._total = 0
        self._lock = threading.Lock()

    def append(self, chunk: str) -> None:
        with self._lock:
            self._lines.append(chunk)
            self._total += len(chunk)
            while self._total > _STDERR_LIMIT and self._lines:
                extra = self._total - _STDERR_LIMIT
                first = self._lines[0]
                if len(first) <= extra:
                    self._total -= len(self._lines.popleft())
                else:
                    self._lines[0] = first[extra:]
                    self._total -= extra

    def text(self) -> str:
        with self._lock:
            return "".join(self._lines).strip()


def _start_stderr_drain(stream: IO[str] | None) -> tuple[_StderrBuffer, threading.Thread | None]:
    """Continuously drain stderr so the docker process cannot block on it."""
    buffer = _StderrBuffer()
    if stream is None:
        return buffer, None

    def drain() -> None:
        try:
            for line in stream:
                buffer.append(line[-_STDERR_LIMIT:])
        except OSError as exc:
            _log.debug("error draining docker stderr: %s", exc)

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    return buffer, thread


def _stop_process(proc: subprocess.Popen[str], stderr_thread: threading.Thread | None) -> None:
    """Terminate docker stats, escalating to kill if it does not exit."""
    try:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except OSError as exc:
            _log.debug("cleanup error: %s", exc)
    except OSError as exc:
        _log.debug("cleanup error: %s", exc)
    finally:
        _unregister_process(proc)

    if stderr_thread is not None:
        stderr_thread.join(timeout=0.5)

    # Release the pipe descriptors instead of waiting on object destruction;
    # stats restarts otherwise leak fds for the life of the wrapper.
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError):
                pipe.close()


def _publish_rows(rows: list[Row], error: str = "") -> None:
    with state.S.lock:
        state.S.rows = rows
        state.S.error = error
        state.S.last_ts = datetime.datetime.now()
        next_history: dict[str, tuple[tuple[float, float], ...]] = {}
        for row in rows:
            previous = state.S.history.get(row.id, ())
            next_history[row.id] = (*previous, (row.cpu_val, row.mem_val))[-HISTORY_SPARKLINE_WIDTH:]
        state.S.history = next_history
        if not rows:
            state.S.sel_id = ""
            state.S.detail_view = False
        elif state.S.highlight and state.S.sel_id not in {row.id for row in rows}:
            state.S.sel_id = rows[0].id
            state.S.detail_view = False
        elif state.S.sel_id and state.S.sel_id not in {row.id for row in rows}:
            state.S.sel_id = ""
            state.S.detail_view = False
    state.S.dirty.set()


def _refresh_details(source: DockerSource, rows: list[Row]) -> None:
    """Maintain the inspect-details cache consumed by the detail view.

    Container metadata is essentially static while a container runs, so
    `docker inspect` is forked only when the detail view is open and the
    selected container is not already cached. Entries for containers that
    left the row set are evicted (same pattern as history pruning).
    """
    live_ids = {row.id for row in rows}
    with state.S.lock:
        detail_view = state.S.detail_view
        sel_id = state.S.sel_id
        cached_ids = set(state.S.details)

    stale_ids = cached_ids - live_ids
    fetched: dict[str, ContainerDetails] = {}
    if detail_view and sel_id in live_ids and sel_id not in cached_ids:
        fetched = _inspect_details(_source_runtime_name(source), [sel_id])

    if not fetched and not stale_ids:
        return
    with state.S.lock:
        for cid in stale_ids:
            state.S.details.pop(cid, None)
        state.S.details.update(fetched)


def _consume_source(source: StatsSource) -> bool:
    """Drive a stats source: group rows into cycles and publish them.

    A cycle is complete when a container ID repeats - docker stats re-emits
    every container once per interval, so a duplicate means the next cycle
    has begun. Heartbeats (None items, yielded by the source while idle)
    carry the two time-based fallbacks inline, with no helper threads:

    - a batch idle for _BATCH_FLUSH_TIMEOUT is published anyway, so a
      single-container host still updates every cycle instead of waiting
      for the next cycle's duplicate ID, and
    - rows are cleared after _STALE_ROWS_TIMEOUT without output, so the
      display does not keep showing data from a daemon that went quiet.

    Returns whether at least one full cycle completed.
    """

    def _flush_batch(snapshot: dict[str, Row]) -> None:
        """Publish a batch to shared state."""
        rows = list(snapshot.values())
        if isinstance(source, DockerSource):
            _refresh_details(source, rows)
        _publish_rows(rows)

    batch: dict[str, Row] = {}
    dirty = False  # batch holds rows not yet published
    published_any = False
    saw_row = False
    completed_any_cycle = False
    last_row_time = time.monotonic()

    for item in source:
        if state.S.quit.is_set():
            break
        now = time.monotonic()

        if item is not None:
            saw_row = True
            last_row_time = now
            if item.id in batch:
                if dirty:
                    _flush_batch(batch)
                    published_any = True
                completed_any_cycle = True
                batch = {}
            batch[item.id] = item
            dirty = True
            continue

        # Heartbeat: no row within the poll interval - check the deadlines.
        if dirty and now - last_row_time >= _BATCH_FLUSH_TIMEOUT:
            _flush_batch(batch)
            published_any = True
            dirty = False
        if now - last_row_time >= _STALE_ROWS_TIMEOUT:
            with state.S.lock:
                has_rows = bool(state.S.rows)
            if has_rows:
                # The stream went quiet while the process is still alive.
                # Publish a notice rather than empty rows so this reads as a
                # stalled daemon, not a genuinely idle host ("no containers
                # running"). Cleared automatically when rows next arrive.
                _publish_rows([], error="no stats received - waiting for daemon...")

    # The stream ended (EOF or quit). A batch that never got published -
    # a single-pass replay file, or a stream that died before its second
    # cycle - is the only data we will ever have, so publish it rather
    # than dropping it. If publishes already happened, the trailing batch
    # is an incomplete cycle and is discarded instead.
    if dirty and not published_any:
        _flush_batch(batch)

    if not saw_row and not state.S.quit.is_set():
        _publish_rows([])

    return completed_any_cycle


def _close_source(source: StatsSource) -> str:
    source.close()
    return source.stderr_text


def _source_runtime_name(source: StatsSource | None) -> str:
    # getattr (not Protocol) because only DockerSource has a runtime;
    # FileSource (replay) has none, so fall back to the configured args.
    runtime = getattr(source, "runtime", "")
    if runtime:
        return runtime
    args = state.S.args
    return args.runtime if args is not None else "docker"


def _default_source_factory() -> StatsSource:
    runtime = "docker"
    args = state.S.args
    if args is not None:
        replay = getattr(args, "replay", None)
        if replay:
            return FileSource(replay)
        runtime = getattr(args, "runtime", runtime)
    return DockerSource(runtime=runtime)


def fetcher_loop(source_factory: Callable[[], StatsSource] | None = None) -> None:
    make_source = source_factory if source_factory is not None else _default_source_factory
    backoff = _BACKOFF_INITIAL

    while not state.S.quit.is_set():
        source: StatsSource | None = None
        stderr_text = ""
        try:
            source = make_source()
            if _consume_source(source):
                backoff = _BACKOFF_INITIAL
        except FileNotFoundError:
            runtime = _source_runtime_name(source)
            with state.S.lock:
                state.S.error = f"{runtime} CLI not found on PATH"
            state.S.dirty.set()
            # Unrecoverable - no point retrying.
            state.S.quit.wait()
            return
        except PermissionError:
            runtime = _source_runtime_name(source)
            with state.S.lock:
                state.S.error = f"permission denied running {runtime}"
            state.S.dirty.set()
            state.S.quit.wait(5)
            continue
        except OSError as exc:
            _log.warning("I/O error reading docker stats: %s", exc)
        finally:
            if source is not None:
                stderr_text = _close_source(source)

        if not state.S.quit.is_set():
            runtime = _source_runtime_name(source)
            error_msg = f"{runtime} stats exited - restarting..."
            if stderr_text:
                error_msg = stderr_text[:120]
                _log.warning("docker stderr: %s", stderr_text)
            with state.S.lock:
                state.S.error = error_msg
            state.S.dirty.set()
            state.S.quit.wait(backoff)
            # Exponential backoff, capped.
            backoff = min(backoff * 2, _BACKOFF_MAX)
