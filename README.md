# Rust vs V

The same small chat app in **Rust / Actix Web** and **V / veb + WebSocket reactor**.
Register a username, choose another user, and chat in real time. Both apps embed
one shared plain HTML/CSS/JS frontend.

## Performance

**V delivered 7–8% more messages per second with 12–15% less CPU per message.
Rust used substantially less memory and had better tail latency for 1 KiB messages.**

| Users · message size · window | Rust msg/s ↑ | V msg/s ↑ | V change |
|---|---:|---:|---:|
| 2 · 64 B · 1 | 15,972 | 17,233 | +7.9% |
| 256 · 64 B · 1 | 62,499 | 67,136 | +7.4% |
| 256 · 64 B · 8 | 204,837 | 221,051 | +7.9% |
| 64 · 1 KiB · 8 | 169,987 | 184,093 | +8.3% |

Each cell below is **Rust / V**; lower is better.

| Same workloads | CPU µs/message ↓ | RAM, MiB RSS ↓ | p95 latency, ms ↓ | p99 latency, ms ↓ |
|---|---:|---:|---:|---:|
| 2 · 64 B · 1 | 45.65 / 39.47 | 4.13 / 24.01 | 0.082 / 0.074 | 0.136 / 0.125 |
| 256 · 64 B · 1 | 29.09 / 25.48 | 17.84 / 71.75 | 3.549 / 3.309 | 5.350 / 5.020 |
| 256 · 64 B · 8 | 8.94 / 7.63 | 19.34 / 73.35 | 8.599 / 7.912 | 12.702 / 10.379 |
| 64 · 1 KiB · 8 | 10.78 / 9.45 | 12.83 / 48.96 | 2.480 / 2.693 | 3.910 / 4.151 |

With **256 idle sockets**, Rust / V used **17.88 / 45.11 MiB RSS**,
**0.17% / 0.50% CPU** (one core = 100%), and **4 / 10 threads**.

Measured **2026-10-06** on an Intel i5-8500, Linux x86_64: eight alternating
trials per app/workload, two server CPUs, separate load-generator CPUs, 2 s warmup
and 6 s measurement. Window = in-flight messages per conversation. Messages are
verified at sender and recipient; latency includes loopback delivery and client
processing. Zero delivery errors. [Trial data and methodology](benchmarks/).

These measurements use the **pre-cleanup binaries**. The cleaned source passes
the correctness suites; it has not been re-benchmarked. Results are specific to
these apps, workloads, and machine.

## Run

Requires **Linux x86_64**, [mise](https://mise.jdx.dev/), Git, GCC, and a
working systemd user session with cgroup v2 memory controls.

```sh
git clone https://github.com/AliOsm/rust-vs-v.git
cd rust-vs-v
mise trust
mise install
mise run setup
mise run dev
```

| App | Stack | URL |
|---|---|---|
| [rust-app/](rust-app/) | Rust 1.99.0 · Actix Web 4.15.0 · actix-ws 0.4.0 | http://localhost:3001 |
| [v-app/](v-app/) | Pinned V 0.5.2 · veb · WebSocket reactor · Boehm GC | http://localhost:3002 |

Open two tabs on the **same port** and register two users. Ctrl+C stops both apps.
Use `mise run dev:rust` or `mise run dev:v` to run one.

The V build compiles the pinned upstream portable compiler (`02d8026`) and applies the
[runtime patch](toolchains/v-runtime.patch) used in the measured implementation.
All build/check tasks run serially with a 1.5 GiB hard memory cap and an early
stop at 1.125 GiB. Generated files stay outside Git.

## Checks and limits

```sh
mise run check          # formatting, Clippy, frame ownership
mise run test           # both apps: protocol, delivery, history, sessions
mise run check:results  # verify published benchmark medians
```

`mise run test:browser` covers desktop/mobile chat with installed Chrome/Chromium
or Playwright Chromium (`mise exec -- uv run playwright install chromium`).

Guest accounts and messages are **in memory** and disappear on restart. Limits:
2,000 users, four sockets per user, 4,096 UTF-8 bytes/message, and 100 messages per
conversation. Rust accepts `HOST`, `PORT`, and `WORKERS`; V accepts `PORT` and
binds all IPv4 interfaces. Both use bounded outbound queues.

Edit [public/](public/) once for both frontends. Dependency licenses are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
