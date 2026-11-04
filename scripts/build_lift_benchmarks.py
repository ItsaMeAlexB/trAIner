"""Build compact lift percentile bands from LiftGauge's CC0 cohorts.

Source: arvidfcjarfalla-prog/liftgauge-strength-data (data CC0, derived from
OpenPowerlifting CC0). Raw JSON is downloaded to data/raw/ and not committed.
Output: data/benchmarks/lift_percentiles.csv (long format, raw equipment).
"""

from __future__ import annotations

import csv
import json
import os

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "liftgauge_cohorts.json")
OUT = os.path.join(ROOT, "data", "benchmarks", "lift_percentiles.csv")
URL = ("https://raw.githubusercontent.com/arvidfcjarfalla-prog/"
       "liftgauge-strength-data/main/data/cohorts.json")
LIFTS = {"squat", "bench", "deadlift"}
PERCENTILES = range(10, 100, 10)


def _download() -> dict:
    os.makedirs(os.path.dirname(RAW), exist_ok=True)
    if not os.path.exists(RAW):
        print(f"downloading {URL}")
        r = requests.get(URL, timeout=120)
        r.raise_for_status()
        with open(RAW, "wb") as f:
            f.write(r.content)
    with open(RAW) as f:
        return json.load(f)


def build() -> None:
    data = _download()
    rows = []
    for key, cell in data["percentile"].items():
        try:
            sex, lift, bw_class, age, equip = key.split("|")
        except ValueError:
            continue
        if sex not in ("m", "f") or lift not in LIFTS or equip != "raw":
            continue
        if age != "all" and not (15 <= int(age) <= 80):
            continue
        if cell.get("n", 0) < 30:
            continue
        for pct in PERCENTILES:
            value = cell.get(f"p{pct}")
            if value is None:
                continue
            rows.append({
                "sex": sex.upper(),
                "lift": lift,
                "weight_class_kg": int(bw_class),
                "age": age,
                "percentile": pct,
                "kg": value,
                "n": cell["n"],
                "equipment": equip,
                "source": "liftgauge-cc0",
            })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    build()
