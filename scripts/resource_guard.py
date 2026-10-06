"""Run one experiment in a verified memory-limited cgroup with an early-stop monitor."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time


def main(args):
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    assert args.command
    report = Path(args.report).resolve() if args.report else (
        Path(args.report_dir).resolve() /
        (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"-{os.getpid()}.json"))
    assert not report.exists(), f"Preserve the existing resource record: {report}"
    if not args.inside:
        # A shared lock prevents accidental concurrent mise/build/benchmark jobs.
        lock = (Path(__file__).resolve().parent.parent / ".resource-guard.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another guarded experiment is running; wait for it to finish")
        command = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                   f"--property=MemoryMax={args.memory_mib}M", "--property=MemorySwapMax=0",
                   sys.executable, str(Path(__file__).resolve()), "--inside",
                   "--memory-mib", str(args.memory_mib), "--timeout", str(args.timeout),
                   "--report", str(report), "--", *args.command]
        return subprocess.call(command)
    entry = next(line.split(":", 2)[2] for line in Path("/proc/self/cgroup").read_text().splitlines()
                 if line.startswith("0::"))
    group = Path("/sys/fs/cgroup") / entry.lstrip("/")
    limit = args.memory_mib * 1024**2
    assert (group / "memory.max").read_text().strip() == str(limit)
    assert (group / "memory.swap.max").read_text().strip() == "0"
    soft_limit = int(limit * .75)
    print(f"Resource guard: {args.memory_mib} MiB hard cap, no swap; "
          f"stop at {soft_limit // 1024**2} MiB or {args.timeout:g}s", flush=True)
    started = time.monotonic()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    child = subprocess.Popen(args.command, start_new_session=True,
                             env={**os.environ, "RUST_VS_V_RESOURCE_GUARD": str(group)})
    reason, peak, processes = None, 0, []

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        while child.poll() is None:
            used = int((group / "memory.current").read_text())
            peak = max(peak, used)
            if used >= soft_limit:
                reason = "early memory stop"
                break
            if time.monotonic() - started >= args.timeout:
                reason = "wall-clock timeout"
                break
            time.sleep(.05)
    except KeyboardInterrupt:
        reason = "interrupted"
    finally:
        if reason:
            for raw_pid in (group / "cgroup.procs").read_text().split():
                try:
                    proc = Path("/proc") / raw_pid
                    status = (proc / "status").read_text()
                    rss = next((line for line in status.splitlines() if line.startswith("VmRSS:")), "")
                    processes.append(dict(pid=int(raw_pid), rss=rss,
                        command=(proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")[:1200]))
                except (FileNotFoundError, ProcessLookupError):
                    pass
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        # Also terminate any descendants left behind after the command exits.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        # Servers/load generators may create their own sessions. They still
        # belong to this experiment's cgroup and must not outlive the guard.
        for raw_pid in (group / "cgroup.procs").read_text().split():
            pid = int(raw_pid)
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        events = (group / "memory.events").read_text()
        record = dict(command=args.command, cwd=os.getcwd(), cgroup=entry,
                      memory_max_bytes=limit, memory_swap_max_bytes=0,
                      sampled_peak_bytes=peak, memory_events=events,
                      memory_peak_bytes=int((group / "memory.peak").read_text()),
                      processes_at_stop=processes,
                      seconds=time.monotonic() - started, reason=reason,
                      returncode=child.returncode)
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
    if reason:
        print("Resource guard stopped the command: " + reason, flush=True)
        return 124
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-mib", type=int, default=1536)
    parser.add_argument("--timeout", type=float, default=600)
    reports = parser.add_mutually_exclusive_group(required=True)
    reports.add_argument("--report")
    reports.add_argument("--report-dir", help="Create a unique timestamped report without overwriting prior runs")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    assert args.memory_mib >= 64 and args.timeout > 0
    raise SystemExit(main(args))
