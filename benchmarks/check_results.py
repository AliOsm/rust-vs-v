"""Recompute the published active-workload summary from all retained trials."""
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
METRICS = (
    "operations_per_second", "p50_ms", "p95_ms", "p99_ms", "cpu_us_per_operation",
    "server_cpu_percent", "client_cpu_percent", "mean_rss_mib", "peak_rss_mib",
    "peak_vms_mib", "peak_threads",
)


def main():
    data = json.loads((ROOT / "results.json").read_text())
    summary = json.loads((ROOT / "summary.json").read_text())
    for item in summary:
        rows = [row for block in data["blocks"]
                if block["retained"] and block["case"] == item["case"]
                for row in block["rows"] if row["app"] == item["app"]]
        assert len(rows) == item["runs"] == 8
        assert all(row["errors"] == 0 and not row.get("failed") for row in rows)
        for key in METRICS:
            median = statistics.median(row[key] for row in rows)
            assert math.isclose(median, item[key], rel_tol=1e-12), (item["app"], key)
        assert min(row["operations_per_second"] for row in rows) == item["minimum"]
        assert max(row["operations_per_second"] for row in rows) == item["maximum"]
    assert len(summary) == 8
    print("Verified eight summaries from 64 retained runs; zero delivery errors.")


if __name__ == "__main__":
    main()
