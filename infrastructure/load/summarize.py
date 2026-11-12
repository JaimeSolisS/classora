"""Summarize one load run directory (#152; run.sh writes it).

    python3 infrastructure/load/summarize.py <run dir> <start epoch> <end epoch>

Reads k6-summary.json (client side), samples.log (host/sample.sh) and
prometheus.txt (prom.sh, server side) and prints Markdown tables: the E1
thresholds with pass/fail, per-container RAM and CPU, host memory and swap,
PostgreSQL connections, and the slowest routes. Standard library only.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

MEM_LIMITS_MIB = {
    "api-1": 512,
    "worker-1": 384,
    "db-1": 640,
    "pgbouncer-1": 32,
    "edge-1": 96,
    "collector-1": 160,
}


def k6_metric(summary: dict, name: str, stat: str) -> float | None:
    metric = summary.get("metrics", {}).get(name)
    if not metric:
        return None
    value = metric.get(stat, metric.get("values", {}).get(stat))
    return float(value) if value is not None else None


def prom(lines: list[str], name: str) -> list[tuple[str, float]]:
    out = []
    for line in lines:
        parts = line.split(" ")
        if len(parts) == 3 and parts[0] == name:
            try:
                out.append((parts[1], float(parts[2])))
            except ValueError:
                continue
    return out


def one(lines: list[str], name: str) -> float | None:
    values = prom(lines, name)
    return values[0][1] if values else None


def fmt_ms(seconds: float | None) -> str:
    return (
        "n/a" if seconds is None or seconds != seconds else f"{seconds * 1000:.0f} ms"
    )


def main() -> int:
    run = Path(sys.argv[1])
    start, end = int(sys.argv[2]), int(sys.argv[3])
    summary = (
        json.loads((run / "k6-summary.json").read_text())
        if (run / "k6-summary.json").exists()
        else {}
    )
    prom_lines = (
        (run / "prometheus.txt").read_text().splitlines()
        if (run / "prometheus.txt").exists()
        else []
    )
    samples = (
        (run / "samples.log").read_text().splitlines()
        if (run / "samples.log").exists()
        else []
    )
    samples = [s.split() for s in samples if s.strip()]
    window = [
        s
        for s in samples
        if len(s) > 2 and s[1].isdigit() and start <= int(s[1]) <= end
    ]

    print(f"### {run.name} ({end - start} s)\n")

    # ---- API, server side
    requests = sum(v for _, v in prom(prom_lines, "requests"))
    status = dict(prom(prom_lines, "status"))
    errors_5xx = sum(v for k, v in status.items() if "code=5" in k)
    get95, uns95 = one(prom_lines, "get_p95"), one(prom_lines, "unsafe_p95")
    pdf95 = max((v for _, v in prom(prom_lines, "pdf_p95") if v == v), default=None)
    k6_failed = k6_metric(summary, "http_req_failed{kind:api}", "value")
    if k6_failed is None:
        k6_failed = k6_metric(summary, "http_req_failed{kind:api}", "rate")
    client_get95 = k6_metric(summary, "http_req_duration{kind:api,method:GET}", "p(95)")
    client_pdf95 = k6_metric(summary, "pdf_duration", "p(95)")
    print("| Measure | Value |\n|---|---|")
    print(
        f"| API requests (server, excl. SSE) | {requests:.0f} ({requests / max(end - start, 1):.1f}/s) |"
    )
    codes = ", ".join(f"{k.split('=')[-1]}: {v:.0f}" for k, v in sorted(status.items()))
    print(f"| Status codes (server) | {codes} |")
    print(
        f"| GET p50 / p95 / p99 (server) | {fmt_ms(one(prom_lines, 'get_p50'))} / {fmt_ms(get95)} / {fmt_ms(one(prom_lines, 'get_p99'))} |"
    )
    print(
        f"| Unsafe p50 / p95 / p99 (server, excl. login) | {fmt_ms(one(prom_lines, 'unsafe_p50'))} / {fmt_ms(uns95)} / {fmt_ms(one(prom_lines, 'unsafe_p99'))} |"
    )
    print(f"| PDF p95 (server, worst route) | {fmt_ms(pdf95)} |")
    print(
        f"| Client GET p95 / PDF p95 (k6, incl. round trip) | {client_get95 or 0:.0f} ms / {client_pdf95 or 0:.0f} ms |"
    )
    print(f"| Client error rate (k6, kind:api) | {100 * (k6_failed or 0):.2f} % |")
    print(
        f"| VUs max (k6, browsing + SSE) | {k6_metric(summary, 'vus_max', 'max') or k6_metric(summary, 'vus_max', 'value')} |"
    )
    print()

    # ---- containers (sampler)
    mem: dict[str, list[float]] = defaultdict(list)
    cpu: dict[str, list[float]] = defaultdict(list)
    for s in window:
        if s[0] == "C" and len(s) == 5:
            mem[s[2]].append(int(s[3]) / 1048576)
            cpu[s[2]].append(float(s[4]))
    if mem:
        print(
            "| Container | RSS avg / peak (MiB) | mem_limit | Peak % of limit | CPU avg / peak (% of one vCPU) |"
        )
        print("|---|---|---|---|---|")
        for name in sorted(mem):
            limit = MEM_LIMITS_MIB.get(name, 0)
            m, c = mem[name], cpu[name]
            pct = f"{100 * max(m) / limit:.0f} %" if limit else "-"
            print(
                f"| {name} | {sum(m) / len(m):.0f} / {max(m):.0f} | {limit} | {pct} |"
                f" {sum(c) / len(c):.0f} / {max(c):.0f} |"
            )
        print()

    # ---- host
    host = [s for s in window if s[0] == "H" and len(s) == 9]
    if len(host) >= 2:
        avail = [int(s[2]) / 1024 for s in host]
        total = int(host[0][3]) / 1024
        secs = int(host[-1][1]) - int(host[0][1])
        swin = (int(host[-1][4]) - int(host[0][4])) * 4 / 1024
        swout = (int(host[-1][5]) - int(host[0][5])) * 4 / 1024
        max_rate = 0.0
        for a, b in zip(host, host[6:]):  # about one minute apart
            dt = int(b[1]) - int(a[1])
            if dt > 0:
                max_rate = max(max_rate, (int(b[4]) - int(a[4])) * 4 / dt)
        load = [float(s[6]) for s in host]
        iowait = (int(host[-1][7]) - int(host[0][7])) / max(
            int(host[-1][8]) - int(host[0][8]), 1
        )
        print("| Host | Value |\n|---|---|")
        print(
            f"| MemAvailable min / avg (MiB of {total:.0f}) | {min(avail):.0f} ({100 * min(avail) / total:.0f} %) / {sum(avail) / len(avail):.0f} |"
        )
        print(f"| Swap in / out over the run (MiB) | {swin:.1f} / {swout:.1f} |")
        print(f"| Swap-in, worst 1-minute average | {max_rate:.1f} KiB/s |")
        print(
            f"| Load (1 min) avg / max | {sum(load) / len(load):.2f} / {max(load):.2f} |"
        )
        print(f"| CPU iowait share | {100 * iowait:.1f} % |")
        print(f"| Samples | {len(host)} over {secs} s |")
        print()

    # ---- PostgreSQL
    conns: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in window:
        if s[0] == "P" and len(s) == 6:
            conns[int(s[1])][f"{s[2]}/{s[3]}/{s[4]}"] += int(s[5])
    if conns:
        peak: dict[str, int] = defaultdict(int)
        for snapshot in conns.values():
            for key, n in snapshot.items():
                peak[key] = max(peak[key], n)
        totals = [sum(v.values()) for v in conns.values()]
        print("| PostgreSQL connections (db/user/state) | Peak |\n|---|---|")
        for key in sorted(peak):
            if key.split("/")[0] in ("classora", "classora_load"):
                print(f"| {key} | {peak[key]} |")
        print(f"| all backends, peak | {max(totals)} |")
        dstats = [
            s for s in window if s[0] == "D" and len(s) == 8 and s[2] == "classora_load"
        ]
        if len(dstats) >= 2:
            a, b = dstats[0], dstats[-1]
            hit, read = int(b[3]) - int(a[3]), int(b[4]) - int(a[4])
            print(
                f"| classora_load cache hit ratio | {100 * hit / max(hit + read, 1):.2f} % |"
            )
            print(
                f"| deadlocks / temp files during the run | {int(b[5]) - int(a[5])} / {int(b[6]) - int(a[6])} |"
            )
            print(
                f"| commits/s | {(int(b[7]) - int(a[7])) / max(int(b[1]) - int(a[1]), 1):.1f} |"
            )
        print()

    # ---- slowest routes, DB and jobs
    routes = sorted(
        ((v, k) for k, v in prom(prom_lines, "route_p95") if v == v), reverse=True
    )[:8]
    if routes:
        print("| Slowest routes (server p95) | p95 |\n|---|---|")
        for v, k in routes:
            print(f"| {k} | {fmt_ms(v)} |")
        print()
    db = prom(prom_lines, "db_op_p95")
    pool = prom(prom_lines, "db_pool_max")
    jobs = [(k, v) for k, v in prom(prom_lines, "job_runs") if v > 0]
    jobs95 = dict(prom(prom_lines, "job_p95"))
    print("| Other (server) | Value |\n|---|---|")
    for k, v in db:
        print(f"| SQL statement p95 {k} | {fmt_ms(v)} |")
    for k, v in pool:
        print(f"| SQLAlchemy pool max {k} | {v:.0f} |")
    for k, v in jobs:
        task = k.split("job_task=")[-1]
        print(
            f"| job runs {k} | {v:.0f} (p95 {fmt_ms(jobs95.get('job_task=' + task))}) |"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
