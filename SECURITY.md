# Security Policy

## Scope

`dsw` is a read-only monitoring tool that wraps the output of `docker stats` (or `podman stats` with `--runtime podman`). It does **not**:

- Create, stop, start, or manage containers
- Modify Docker or Podman configuration
- Send telemetry or make network requests
- Read container logs, volume contents, or secrets
- Require elevated privileges beyond what `docker stats` needs

## What dsw accesses

- **Container runtime CLI**: dsw invokes `docker stats` (and, while the detail view is open, `docker inspect`) via subprocess using whichever `docker` binary is on your `$PATH`. With `--runtime podman`, the same applies to the `podman` binary.
- **Host resource data**: CPU %, memory usage, network I/O, and block I/O as reported by the runtime.
- **Container metadata**: from `docker inspect`, only the fields shown in the detail view are extracted (image, status, created time, restart count, port mappings, mount paths, network name, IP address). Environment variables and other inspect output are never displayed or stored.
- **Terminal state**: dsw sets the terminal to cbreak mode for keyboard input and restores it on exit.
- **Local files**: only when the hidden development flags are used - `--debug FILE` writes a log file, `--replay FILE` reads a recorded stats stream. Neither is active by default.

## Docker permissions

Running `docker stats` typically requires membership in the `docker` group or root access. Podman commonly runs rootless and needs no extra privileges. In both cases dsw inherits whatever permissions your shell environment already has - it never elevates.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

If you discover a security issue, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities.
2. Email me at **github@dimitrisper.xyz** with:
   - A description of the vulnerability
   - Steps to reproduce
   - Potential impact assessment
3. You will receive acknowledgment within 48 hours.
4. A fix will be developed and released as soon as practical, with credit given to the reporter (unless anonymity is requested).

## Known trust boundaries

- dsw trusts whatever `docker` (or `podman`) binary is on `$PATH`. If an attacker can place a malicious binary with that name earlier on your PATH, dsw will execute it - the same trust you extend when running `docker` yourself. Verify your PATH if this is a concern in your environment.
- dsw does not validate Docker daemon TLS certificates or authentication. It relies on the Docker CLI's own security configuration (contexts, `DOCKER_HOST`, TLS settings).
- Container names and stats fields come from the daemon and are treated as untrusted: ANSI escape sequences and control characters are stripped before anything is rendered to your terminal.
