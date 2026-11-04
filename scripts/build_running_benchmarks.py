"""Build compact running percentile bands from the Italian Athletics dataset.

Source: Kaggle `davideliu/italian-athletics-historical-best-performances`
License: CC0. The raw 20 MB zip is downloaded to data/raw/ and not committed.
Output: data/benchmarks/running_percentiles.csv (long format).
"""

from __future__ import annotations

import csv
import io
import os
import statistics
import sys
import zipfile
from collections import defaultdict

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "italian-athletics.zip")
OUT = os.path.join(ROOT, "data", "benchmarks", "running_percentiles.csv")
URL = ("https://www.kaggle.com/api/v1/datasets/download/"
       "davideliu/italian-athletics-historical-best-performances")
EVENTS = {"400m": 400, "800m": 800, "5000m": 5000, "10000m": 10000}
PERCENTILES = range(10, 100, 10)


def _download() -> None:
    os.makedirs(os.path.dirname(RAW), exist_ok=True)
    if os.path.exists(RAW):
        return
    print(f"downloading {URL}")
    r = requests.get(URL, timeout=300)
    r.raise_for_status()
    with open(RAW, "wb") as f:
        f.write(r.content)


def _time_seconds(value: str) -> float | None:
    try:
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return None


def _age_band(age: int) -> str:
    if age >= 80:
        return "80+"
    lo = (age // 5) * 5
    return f"{lo}-{lo + 4}"


def build() -> None:
    _download()
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    with zipfile.ZipFile(RAW) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            for row in csv.DictReader(text):
                event = row.get("event", "")
                if event not in EVENTS:
                    continue
                sex = row.get("sex", "").strip().upper()
                if sex not in ("M", "F"):
                    continue
                try:
                    birth = int(float(row["birth-year"]))
                    year = int(row["date"][:4])
                    age = year - birth
                except (ValueError, KeyError, TypeError):
                    continue
                if not 15 <= age <= 90:
                    continue
                seconds = _time_seconds(row.get("time", ""))
                if not seconds or seconds <= 0:
                    continue
                groups[(sex, event, _age_band(age))].append(seconds)
                groups[(sex, event, "all")].append(seconds)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows = []
    for (sex, event, band), values in sorted(groups.items()):
        if len(values) < 30:
            continue
        values.sort()
        cuts = statistics.quantiles(values, n=10, method="inclusive")
        for pct, value in zip(PERCENTILES, cuts):
            rows.append({
                "sex": sex,
                "age_band": band,
                "event": event,
                "distance_m": EVENTS[event],
                "percentile": pct,
                "time_s": round(value, 2),
                "n": len(values),
                "source": "italian-athletics-cc0",
            })

    with open(OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    try:
        build()
    except Exception as exc:  # noqa: BLE001
        print(f"build failed: {exc}", file=sys.stderr)
        raise
