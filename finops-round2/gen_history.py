"""Generate a realistic 30-day Prometheus-style memory history for checkout-service
(2 replicas), then a compact summary a FinOps tool would hand an engineer.

The pattern the data encodes: a clean daily rhythm - quiet overnight (~150Mi),
a business-hours rise, and a consistent evening peak (~430-465Mi) as the cart
cache fills. Fridays run a little hotter. Both replicas healthy the whole month.

What the data does NOT contain (the whole episode): the product launch that
hasn't happened yet. P99 of last month is a floor, not a ceiling.

Writes:
  history/checkout_30d.csv   - hourly per-replica working set (raw, for the repo)
  history/summary.txt        - what we hand the AI (daily peaks + percentiles)
  history/daily_peaks.json   - 30 daily peak values (for the graph card)
"""
import json
import math
import random
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "history"
OUT.mkdir(exist_ok=True)

random.seed(1730)  # reproducible

REPLICAS = ["checkout-6d9-a", "checkout-6d9-b"]
DAYS = 30
# a memory working set (MiB) for a given hour-of-day (0..23), before per-day noise
def hour_base(h):
    # overnight trough ~150, business rise, evening peak ~ +290 around 20:00
    peak = 300 * math.exp(-((h - 20) ** 2) / (2 * 3.2 ** 2))   # gaussian around 8pm
    morning = 90 * math.exp(-((h - 11) ** 2) / (2 * 2.5 ** 2))  # small lunchtime bump
    return 150 + peak + morning


rows = [("timestamp_day", "hour", "replica", "mem_mib", "cpu_millicores")]
daily_peaks = []
all_samples = []
for day in range(1, DAYS + 1):
    dow = (day - 1) % 7  # 0=Mon ... 4=Fri, 5-6 weekend
    friday = (dow == 4)
    weekend = dow >= 5
    day_factor = 1.06 if friday else (0.9 if weekend else 1.0)
    day_peak = 0
    for h in range(24):
        for r in REPLICAS:
            base = hour_base(h) * day_factor
            noise = random.gauss(0, 8)
            mem = max(120, base + noise)
            # replicas are near-symmetric (LB spreads traffic); tiny per-replica offset
            mem += random.gauss(0, 4)
            mem = round(mem, 1)
            cpu = round(max(2, 6 + (mem - 150) * 0.03 + random.gauss(0, 1.5)), 1)
            rows.append((day, h, r, mem, cpu))
            all_samples.append(mem)
            day_peak = max(day_peak, mem)
    daily_peaks.append(round(day_peak, 1))

# raw csv
with open(OUT / "checkout_30d.csv", "w", encoding="utf-8") as f:
    for row in rows:
        f.write(",".join(str(x) for x in row) + "\n")

# percentiles
srt = sorted(all_samples)
def pct(p): return round(srt[min(len(srt) - 1, int(p / 100 * len(srt)))], 1)
p50, p90, p95, p99, mx = pct(50), pct(90), pct(95), pct(99), round(max(all_samples), 1)

json.dump({"daily_peaks": daily_peaks, "p50": p50, "p95": p95, "p99": p99, "max": mx},
          open(OUT / "daily_peaks.json", "w"), indent=1)

# the summary we hand the AI (what a FinOps dashboard would show)
lines = []
lines.append("checkout-service  -  memory working set, last 30 days (Prometheus)")
lines.append("replicas: 2/2 healthy for the entire window (0 restarts, 0 evictions)")
lines.append("current config: memory request 512Mi, limit 768Mi, per replica")
lines.append("")
lines.append("Per-replica memory percentiles across the whole month (MiB):")
lines.append(f"  p50={p50}   p90={p90}   p95={p95}   p99={p99}   max={mx}")
lines.append("")
lines.append("CPU: p99 ~ 22 millicores per replica (negligible).")
lines.append("")
lines.append("Daily peak per-replica memory (MiB), day 1 -> 30:")
for wk in range(0, DAYS, 10):
    chunk = daily_peaks[wk:wk + 10]
    lines.append("  " + "  ".join(f"{v:5.0f}" for v in chunk))
lines.append("")
lines.append("Pattern: quiet overnight (~150Mi), consistent evening peak (~450Mi as the")
lines.append("cart cache fills), Fridays slightly hotter. Stable and predictable all month.")
(OUT / "summary.txt").write_text("\n".join(lines), encoding="utf-8")

print("wrote history/checkout_30d.csv (", len(rows) - 1, "rows )")
print("percentiles: p50", p50, "p95", p95, "p99", p99, "max", mx)
print("daily peaks:", daily_peaks)
