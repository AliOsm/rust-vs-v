# Recorded comparison

- [results.json](results.json): all 33 matched blocks, including the one rejected
  for interference; numerical trial fields are unchanged. Local paths, build
  manifests, logs, and time-series samples are omitted.
- [summary.json](summary.json): medians from the 32 retained blocks (64 runs).
- [idle.json](idle.json): four trials per app with 256 idle connections.
- `mise run check:results` recomputes the active-workload medians.

**Machine:** Intel Core i5-8500, six logical CPUs, 15.03 GiB RAM, Linux 7.0.0,
GCC/Boehm for V. Servers use CPUs 0–1, load generator 2–4, controller 5.
CPU affinity does not isolate the machine from background work.

**Method:** eight alternating trials/app/workload; fresh server per trial;
2 s warmup, 6 s measured; registration and setup excluded. Require three quiet
seconds before each run. Repeat a whole matched block for compiler overlap or
unrelated average CPU above 125% of one core. Keep all attempts and slow valid
runs; never retry delivery failures.

Throughput counts chat messages with validated sender echoes and recipient
delivery. CPU is server CPU time/message. RSS is the median of each run's mean
resident memory. Latency is end-to-end loopback time in the closed-loop native
Rust load generator. p95/p99 columns are medians of per-run percentiles.

V used compiler `4e8228f`, `-new-compiler -prod -cc gcc -gc boehm -nocache`, and the
JSON/WebSocket runtime changes in `toolchains/`. Rust used 1.99.0 and the release
profile in `rust-app/Cargo.toml`. Binary/source hashes and the original raw-file
hash are retained in `results.json`.

The numbers describe the versions measured before the app cleanup. This export
supports checking the published comparison; the development archive and full
historical rebuild harness remain local. No new performance run was performed
to publish this repository.
