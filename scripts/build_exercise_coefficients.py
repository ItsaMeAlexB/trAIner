"""Extract Hevy Ranks GROUP_COEFFS into data/exercise_coefficients.json.

Source: github.com/BenjiPy/hevy-ranks, src/engine.js (MIT).
"""

from __future__ import annotations

import json
import os
import re

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "exercise_coefficients.json")
URL = "https://raw.githubusercontent.com/BenjiPy/hevy-ranks/main/src/engine.js"


def _object_body(src: str, marker: str) -> str:
    start = src.index(marker) + len(marker)
    while src[start] != "{":
        start += 1
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(src)):
        ch = src[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise ValueError("unbalanced GROUP_COEFFS object")


def _to_json(body: str) -> dict:
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"([{,]\s*)([A-Za-z_][\w]*)\s*:", r'\1"\2":', body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


def build() -> None:
    src = requests.get(URL, timeout=60).text
    coeffs = _to_json(_object_body(src, "const GROUP_COEFFS = "))
    payload = {
        "_note": ("Hevy exercise-to-exercise conversion ratios. Reference lift "
                  "coefficient is 1.0; reference_e1RM = logged_e1RM / c."),
        "source": "github.com/BenjiPy/hevy-ranks src/engine.js",
        "license": "MIT",
        "GROUP_COEFFS": coeffs,
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    print(f"wrote {sum(len(v) for v in coeffs.values())} entries to {OUT}")


if __name__ == "__main__":
    build()
