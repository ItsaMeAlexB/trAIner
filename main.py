"""Daily adaptive health & fitness program manager.

README-faithful implementation of the trAIner pipeline. DeepSeek is the sole
reasoning engine; Google Sheets (6 tabs) is the data store; Telegram is a
one-way delivery channel. Runs once per day at 05:00 UTC via
`.github/workflows/daily.yml`.

Daily pipeline (README "Daily pipeline"):
  1. Fetch weather (Open-Meteo) for the user's location.
  2. Load rolling windows (last 5 weeks) of Activities, Health, Diet, Pantry.
  3. Advance repeating blocks (increment Cycle_day; DeepSeek handles the rest).
  4. Collapse logs -> 1RM (strength-utils + Hevy coefficients), runs -> flat /
     perfect-weather equivalent (calcpace_port), other activities logged apart.
  5. DeepSeek single-prompt run (one message, no clarification round-trip).
  6. Write back recommendations into the left (Recommended) columns.
  7. Deliver the plan + weather to Telegram.
  Every 4 weeks a second Telegram message carries the long-term review.

This module is deliberately side-effectful: there is no dry-run mode (README).
"""

from __future__ import annotations

import csv
import functools
import json
import logging
import os
import re
import statistics
from datetime import datetime, timedelta, timezone

import requests

import calcpace_port
from strength_utils import estimate_1rm

log = logging.getLogger("trainer")

# Fine-grained retention. The progression table below is the long-term memory;
# raw logs only need to cover the current mesocycle, not a year.
WINDOW_DAYS = {"exercise": 14, "diet": 7, "health": 35}
WEEKLY_SUMMARY_WEEKS = 12
PROGRESSION_WEEKS = 104  # 2 years, one ISO week per row

# Defaults; load_settings() allows env overrides (billed research is metered).
RESEARCH_SEARCH_BUDGET = 4
RESEARCH_FETCH_BUDGET = 2
DEEPSEEK_MAX_TOOL_ROUNDS = 6
DEEPSEEK_MAX_TOKENS = 8192

TAB_STRATEGY = "Strategy"
TAB_HEALTH = "Health"
TAB_PANTRY = "Pantry"
TAB_DIET = "Diet"
TAB_EXERCISE = "Exercise"
TAB_PATTERNS = "Patterns"
TAB_CHARTS = "Charts"
TAB_DASHBOARD = "Dashboard"
TABS = [TAB_STRATEGY, TAB_HEALTH, TAB_PANTRY, TAB_DIET, TAB_EXERCISE,
        TAB_PATTERNS, TAB_CHARTS, TAB_DASHBOARD]

# Appended to the daily prompt when the 4-week review is due (README "Patterns &
# 4-week review"): research strategies against current evidence, incl. PubMed.
REVIEW_PROMPT_TAIL = (
    "\\n\\nA 4-week review is due this run. Use web search - including PubMed "
    "via site:pubmed.ncbi.nlm.nih.gov - to check the standing Strategy against "
    "current evidence. Fill longterm_review, and emit strategy_updates to add, "
    "modify, or (status 'removed') delete Strategy entries as appropriate."
)

DATA_DIR = "data"
COEFF_FILE = os.path.join(DATA_DIR, "exercise_coefficients.json")
HARDY_FILE = os.path.join(DATA_DIR, "strength-report-2026.csv")
RUNNING_BENCH_FILE = os.path.join(DATA_DIR, "benchmarks", "running_percentiles.csv")
LIFT_BENCH_FILE = os.path.join(DATA_DIR, "benchmarks", "lift_percentiles.csv")

# Strategy rows are keyed by Category (README "Sheets schema").
STRATEGY_COLS = ["Category", "Detail", "Citations", "Status", "Updated"]
STATE_CATEGORY = "__state__"  # internal counters (last review date etc.)

# Health is a column-per-day tab; rows are health fields (README schema).
HEALTH_FIELDS = [
    "Age", "Birth_year", "Gender", "Height_cm", "Weight_kg", "Latitude",
    "Longitude", "Location", "Sleep_hours", "Sleep_quality",
    "Last_cycle_start", "Cycle_length_avg", "Cycle_day", "Injuries",
    "Medical_events", "Retinol", "Dermarolling", "Free_text", "Complete",
]
# These fields carry forward unchanged each new day (edited manually when they
# change, e.g. the user travels -> Location).
CARRY_FORWARD = {"Age", "Birth_year", "Gender", "Height_cm", "Latitude",
                 "Longitude", "Location", "Last_cycle_start",
                 "Cycle_length_avg"}

# Diet and Exercise are two-columns-per-day tabs: Recommended | Done.
# Completeness is a row inside the day block, so no third column is needed.
DIET_ROWS = [
    "Target_calories", "Macros_micros_target", "Meal_1", "Meal_2", "Meal_3",
    "Snacks", "Water_litres", "Supplements", "Pantry_items_to_use", "Notes",
    "Complete",
]
EXERCISE_ROWS = [
    "Gym_session", "Run", "Other_activities", "Plyo_isometric_sprint", "Notes",
    "Complete",
]

# Blocks below the daily area. Header rows are 1-based; data starts one row down.
EXERCISE_SUMMARY_HEADERS = [
    "Week_start", "Focus", "Sessions_by_group", "Key_loads", "Niche",
    "Adherence", "Strategy",
]
DIET_SUMMARY_HEADERS = [
    "Week_start", "Avg_calories", "Protein_g", "Adherence_pct", "Notes",
]
HEALTH_SUMMARY_HEADERS = [
    "Week_start", "Sleep_avg_h", "Sleep_median_h", "Sleep_quality_avg",
    "Weight_kg", "Cycle_phase", "Notes",
]
PROGRESSION_HEADERS = [
    "Year", "Week", "Week_start", "n_lifts", "n_runs", "Bodyweight_kg",
    "Squat_e1RM", "Bench_e1RM", "Posterior_e1RM", "OHP_e1RM", "Pull_e1RM",
    "5K_equiv_s", "400m_equiv_s", "Jump_cm", "BroadJump_cm", "Sprint_10m_s",
    "Mobility", "Source", "Strategy",
]

EXERCISE_SUMMARY_HEADER_ROW = len(EXERCISE_ROWS) + 3          # 9
DIET_SUMMARY_HEADER_ROW = len(DIET_ROWS) + 3                  # 14
HEALTH_SUMMARY_HEADER_ROW = len(HEALTH_FIELDS) + 3            # 21
PROGRESSION_HEADER_ROW = (
    EXERCISE_SUMMARY_HEADER_ROW + WEEKLY_SUMMARY_WEEKS + 2    # 23
)
NICHE_ROW = len(EXERCISE_ROWS) + 2                            # 8

# Patterns (README): deduplicated significant events, reviewed every 4 weeks.
PATTERN_COLS = ["Date", "Event", "Detail", "Type", "Status"]

# Main movement patterns used as the stable progression benchmarks.
BENCHMARK_LIFTS = {
    "Squat": ("squat", "hack squat", "leg press", "lunge", "split squat",
              "leg extension"),
    "Bench": ("bench", "chest press", "push-up", "pushup", "dip"),
    "Posterior": ("deadlift", "rdl", "romanian", "good morning", "hip thrust",
                  "glute bridge", "hamstring curl", "nordic"),
    "OHP": ("overhead press", "ohp", "shoulder press", "military press",
            "arnold press", "lateral raise"),
    "Pull": ("pull-up", "pullup", "chin-up", "chinup", "row", "pulldown"),
}

# Chart data is written to its own tab; charts on Dashboard reference it.
PERCENTILE_BANDS = [10, 20, 30, 40, 50, 60, 70, 80, 90]
CHART_METRICS = [
    ("5K", "5K_equiv_s", "running", "5000m"),
    ("400m", "400m_equiv_s", "running", "400m"),
    ("Squat", "Squat_e1RM", "lift", "squat"),
    ("Bench", "Bench_e1RM", "lift", "bench"),
    ("Posterior", "Posterior_e1RM", "lift", "deadlift"),
    ("OHP", "OHP_e1RM", "lift", "ohp"),
    ("Pull", "Pull_e1RM", "lift", "pull"),
]
CHART_HEADERS = ["Week_start"]
for _label, _col, _kind, _key in CHART_METRICS:
    CHART_HEADERS.append(f"{_label}_user")
    CHART_HEADERS += [f"{_label}_P{p}" for p in PERCENTILE_BANDS]
CHART_WEEKS = PROGRESSION_WEEKS




def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no extra dependency). Existing env wins."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and not os.environ.get(key):
            os.environ[key] = value


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("trainer.log")],
    )


def _find_service_account_json() -> str | None:
    """Locate a local service-account JSON if no env credentials are set."""
    for name in os.listdir("."):
        if not name.endswith(".json"):
            continue
        try:
            with open(name) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("type") == "service_account" and d.get("private_key"):
            return name
    return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_settings() -> dict:
    load_dotenv()
    sheet_json = os.environ.get("GOOGLE_SHEETS_JSON")
    sheet_key = os.environ.get("GOOGLE_SHEETS_KEY")
    if not sheet_json:
        path = _find_service_account_json()
        if path:
            with open(path) as f:
                d = json.load(f)
            sheet_key = sheet_key or d.get("SHEET_ID")
            sheet_json = json.dumps(d)
    return {
        "spreadsheet_json": sheet_json,
        "spreadsheet_key": sheet_key,
        "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY"),
        "deepseek_base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "deepseek_model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "deepseek_max_tokens": _env_int("DEEPSEEK_MAX_TOKENS", DEEPSEEK_MAX_TOKENS),
        "deepseek_max_tool_rounds": _env_int("DEEPSEEK_MAX_TOOL_ROUNDS", DEEPSEEK_MAX_TOOL_ROUNDS),
        "research_search_budget": _env_int("RESEARCH_SEARCH_BUDGET", RESEARCH_SEARCH_BUDGET),
        "research_fetch_budget": _env_int("RESEARCH_FETCH_BUDGET", RESEARCH_FETCH_BUDGET),
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "usda_api_key": os.environ.get("USDA_API_KEY", ""),
    }



def _col_letter(n: int) -> str:
    """1-based column index -> spreadsheet letters (e.g. 1->A, 28->AB)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class Sheets:
    """Google Sheets client with rolling-column-window helpers.

    Diet and Activities are two-columns-per-day tabs (Recommended | Done);
    Health is a one-column-per-day tab. On overwrite (README "Data retention
    and windows") the oldest day's columns are genuinely deleted, the rest
    shift left, and the new day is appended at the right.
    """

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, settings: dict) -> None:
        key = settings["spreadsheet_key"]
        raw = settings["spreadsheet_json"]
        if raw:
            info = json.loads(raw)
            if not key:
                key = info.pop("SHEET_ID", None)
            info.pop("SHEET_ID", None)
        else:
            info = None
        if not info or not key:
            raise RuntimeError("GOOGLE_SHEETS_JSON and GOOGLE_SHEETS_KEY are required")

        from google.oauth2 import service_account
        import gspread

        self.creds = service_account.Credentials.from_service_account_info(
            info, scopes=self.SCOPES
        )
        self.gc = gspread.authorize(self.creds)
        self.workbook = self.gc.open_by_key(key)

    def ws(self, name: str):
        return self.workbook.worksheet(name)

    def read_rows(self, name: str, headers: list[str]) -> list[dict]:
        rows = self.ws(name).get_all_values()
        out: list[dict] = []
        for r in rows[1:]:
            if not any(cell.strip() for cell in r):
                continue
            out.append({headers[i]: r[i] if i < len(r) else ""
                        for i in range(len(headers))})
        return out

    def append_row(self, name: str, values: dict, headers: list[str]) -> None:
        row = [str(values.get(h, "")) for h in headers]
        self.ws(name).append_row(row)

    @staticmethod
    def _blocks(header: list[str], day_width: int) -> list[tuple[str, int]]:
        """Return (date, 1-based start column) for each day block in row 1."""
        blocks: list[tuple[str, int]] = []
        idx = 1  # skip column A (label)
        while idx < len(header):
            h = (header[idx] or "").strip()
            if not h:
                break
            blocks.append((h.split()[0], idx + 1))
            idx += day_width
        return blocks

    def rolling_ensure_day(self, name: str, day_width: int, today: str,
                           label_rows: list[str],
                           window_days: int = 35) -> dict:
        """Ensure `today`'s day block exists (rolling overwrite)."""
        ws = self.ws(name)
        header = ws.row_values(1)
        if not header or not (header[0] or "").strip():
            ws.update([["Day"]], "A1")
            for i, lab in enumerate(label_rows, start=2):
                ws.update([[lab]], f"A{i}")
            header = ws.row_values(1)
        blocks = self._blocks(header, day_width)
        while len(blocks) >= window_days:
            start = blocks[0][1]
            ws.delete_columns(start, start + day_width - 1)
            header = ws.row_values(1)
            blocks = self._blocks(header, day_width)
        for date, start in blocks:
            if date == today:
                return {"date": date, "start": start, "created": False,
                        "cols": list(range(start, start + day_width))}
        new_start = 1 + 1 + len(blocks) * day_width
        block_header = [""] * day_width
        block_header[0] = today
        ws.update([block_header], f"{_col_letter(new_start)}1")
        return {"date": today, "start": new_start, "created": True,
                "cols": list(range(new_start, new_start + day_width))}


    def rolling_history(self, name: str, day_width: int) -> list[dict]:
        """Return all day blocks as [{date, cols:[cell lists]}], oldest last."""
        header = self.ws(name).row_values(1)
        blocks = self._blocks(header, day_width)
        if not blocks:
            return []
        values = self.ws(name).get_all_values()
        out = []
        for date, start in blocks:
            cols = []
            for c in range(start, start + day_width):
                col_vals = []
                for r in values[1:]:
                    i = c - 1
                    col_vals.append(r[i] if i < len(r) else "")
                cols.append(col_vals)
            out.append({"date": date, "cols": cols})
        return out

    def read_table(self, name: str, header_row: int, headers: list[str],
                   max_rows: int) -> list[dict]:
        """Read a fixed table whose header is at `header_row` (1-based)."""
        values = self.ws(name).get_all_values()
        out: list[dict] = []
        # row 1 is index 0, so the first data row is index == header_row
        for r in values[header_row:header_row + max_rows]:
            if not any(cell.strip() for cell in r):
                continue
            out.append({h: r[i] if i < len(r) else ""
                        for i, h in enumerate(headers)})
        return out

    def upsert_table_row(self, name: str, header_row: int, headers: list[str],
                         key_header: str, values: dict, max_rows: int) -> None:
        """Update a table row matched on `key_header`, else append/roll."""
        ws = self.ws(name)
        all_values = ws.get_all_values()
        key_idx = headers.index(key_header)
        key = str(values.get(key_header, "")).strip()
        target = None
        for i in range(header_row, min(len(all_values), header_row + max_rows)):
            r = all_values[i]
            if len(r) > key_idx and r[key_idx].strip() == key:
                target = i + 1
                break
        if target is None:
            for i in range(header_row, header_row + max_rows):
                r = all_values[i] if i < len(all_values) else []
                if not any(cell.strip() for cell in r):
                    target = i + 1
                    break
        if target is None:
            # Full table: drop the oldest data row so it stays a rolling window.
            ws.delete_rows(header_row + 1)
            target = header_row + max_rows
        row = [str(values.get(h, "")) for h in headers]
        ws.update([row], f"A{target}:{_col_letter(len(headers))}{target}")

    def state_get(self, category: str) -> str:
        for r in self.read_rows(TAB_STRATEGY, STRATEGY_COLS):
            if r["Category"] == STATE_CATEGORY and r["Detail"].startswith(category):
                return r["Detail"].split("=", 1)[1]
        return ""

    def state_set(self, category: str, value: str) -> None:
        header4 = self.ws(TAB_STRATEGY).row_values(1)
        if header4 != STRATEGY_COLS:
            self.ws(TAB_STRATEGY).update([STRATEGY_COLS], "A1")
        detail = f"{category}={value}"
        rows = self.read_rows(TAB_STRATEGY, STRATEGY_COLS)
        for i, r in enumerate(rows, start=2):
            if r["Category"] == STATE_CATEGORY and r["Detail"].startswith(category):
                self.ws(TAB_STRATEGY).update(
                    [[detail, "", "internal", datetime.now(timezone.utc).isoformat()]],
                    f"B{i}:E{i}")
                return
        self.append_row(TAB_STRATEGY, {"Category": STATE_CATEGORY, "Detail": detail,
                                       "Citations": "", "Status": "internal",
                                       "Updated": datetime.now(timezone.utc).isoformat()},
                        STRATEGY_COLS)



# ---------------------------------------------------------------------------
# Weather (Open-Meteo, README step 1)
# ---------------------------------------------------------------------------
def fetch_weather(sheets: Sheets, today: str) -> str:
    """Fetch today's weather for the user's location from Open-Meteo.

    Coordinates come from the Health sheet (Latitude/Longitude, carried
    forward and edited manually if the user travels). Returns a short text
    summary consumed by the DeepSeek prompt and the Telegram message.
    """
    lat = lon = None
    for day in sheets.rolling_history(TAB_HEALTH, 1):
        col = day["cols"][0]
        for field, row in zip(HEALTH_FIELDS, col):
            if field == "Latitude" and row.strip():
                lat = row.strip()
            elif field == "Longitude" and row.strip():
                lon = row.strip()
    if not lat or not lon:
        log.warning("No latitude/longitude in Health; skipping weather.")
        return "Location not set - no weather available."

    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "hourly": "temperature_2m,precipitation_probability,"
                              "precipitation,weathercode",
                    "timezone": "auto", "forecast_days": 16},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
    except requests.RequestException as e:
        log.error("Weather fetch failed: %s", e)
        return "Weather unavailable."

    times = d.get("hourly", {}).get("time", [])
    temps = d.get("hourly", {}).get("temperature_2m", [])
    pop = d.get("hourly", {}).get("precipitation_probability", [])
    precip = d.get("hourly", {}).get("precipitation", [])
    # Summarise today's daylight hours for the current day.
    lines = []
    for i, t in enumerate(times):
        if t[:10] != today:
            continue
        hour = int(t[11:13])
        if 8 <= hour <= 22 and temps[i] is not None:
            summary = f"{hour:02d}:00 {temps[i]:.0f}C"
            if pop and i < len(pop) and pop[i] is not None and pop[i] >= 30:
                summary += f" rain{pop[i]:.0f}%"
            elif precip and i < len(precip) and float(precip[i] or 0) > 0:
                summary += " rain"
            lines.append(summary)
    if not lines:
        return "No hourly weather for today."
    # High-level narrative for the Telegram one-liner.
    day_temps = [temps[i] for i, t in enumerate(times)
                 if t[:10] == today and temps[i] is not None]
    return " | ".join(lines) + (f" (max {max(day_temps):.0f}C)" if day_temps else "")


# ---------------------------------------------------------------------------
# Repeating blocks (README step 3)
# ---------------------------------------------------------------------------
def advance_repeating_blocks(sheets: Sheets, today: str) -> None:
    """Increment Cycle_day and carry forward static Health fields.

    Repeating blocks (menstrual cycle, retinol, dermarolling) are managed by
    DeepSeek via the prompt; the code's concrete action here is advancing
    Cycle_day and copying fields that must persist into the new day column
    (age, gender, height, location, and cycle anchors).
    """
    block = sheets.rolling_ensure_day(TAB_HEALTH, 1, today, HEALTH_FIELDS)
    start = block["start"]
    if not block["created"]:
        log.info("Health column for %s already advanced; skipping.", today)
        return
    prev_one = start - 1     # 1-based previous data column (col A=1 is labels)
    previous: dict[str, str] = {}
    if prev_one > 1:         # no real data column precedes the first day
        prev_zero = prev_one - 1  # 0-based index for get_all_values
        values = sheets.ws(TAB_HEALTH).get_all_values()
        for r in values[1:]:
            field = r[0].strip()
            if prev_zero < len(r) and r[prev_zero] != "":
                previous[field] = str(r[prev_zero])
    updates: dict[str, str] = {}
    for field in CARRY_FORWARD:
        updates[field] = previous.get(field, "")
    # Auto-increment Cycle_day.
    prev_cycle = previous.get("Cycle_day", "")
    updates["Cycle_day"] = str(int(prev_cycle) + 1) if prev_cycle.isdigit() else "1"
    for field, value in updates.items():
        row = HEALTH_FIELDS.index(field) + 2  # row 1 is header
        sheets.ws(TAB_HEALTH).update([[value]], f"{_col_letter(start)}{row}")
    log.info("Advanced repeating blocks (Cycle_day=%s) into %s", updates["Cycle_day"], today)


# ---------------------------------------------------------------------------
# Log collapse (README step 4)
# ---------------------------------------------------------------------------
def _load_coefficients() -> dict:
    """Hevy GROUP_COEFFS (exercise->exercise conversion ratios), if committed."""
    try:
        with open(COEFF_FILE) as f:
            d = json.load(f)
        return d.get("GROUP_COEFFS", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _hardy_percentiles() -> dict:
    out = {}
    try:
        with open(HARDY_FILE) as f:
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) != 6 or parts[0] != "percentiles":
                    continue
                exercise, metric, value = parts[1], parts[4], parts[5]
                try:
                    out.setdefault(exercise, {})[metric] = float(value)
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _load_hardy() -> dict:
    """Minimal Hardy overlay: exercise -> {milestone -> {metric: value}}."""
    idx = {}
    try:
        with open(HARDY_FILE) as f:
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) != 6 or parts[0] != "milestones":
                    continue
                ex, milestone, metric, value = parts[1], parts[2], parts[4], parts[5]
                idx.setdefault(ex, {}).setdefault(milestone, {})[metric] = value
    except OSError:
        pass
    return idx


def _benchmark_lift(exercise: str) -> str | None:
    """Map a free-text exercise name to a stable progression benchmark."""
    ex = exercise.lower().strip()
    for benchmark, keys in BENCHMARK_LIFTS.items():
        if any(key in ex for key in keys):
            return benchmark
    return None


def _lift_coefficient(exercise: str) -> float:
    """Hevy coefficient relative to the group reference; 1.0 if unknown."""
    ex = exercise.lower()
    for entries in _load_coefficients().values():
        for entry in entries:
            if any(key in ex for key in entry.get("k", [])):
                try:
                    return float(entry.get("c", 1.0)) or 1.0
                except (TypeError, ValueError):
                    return 1.0
    return 1.0




def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Least-squares fit y = slope*x + intercept; None if <2 distinct points."""
    n = len(xs)
    if n < 2:
        return None
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    denom = sum((x - xbar) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / denom
    return slope, ybar - slope * xbar


def _parse_time(parts: list[str]) -> int:
    """Convert ['25','30'] or ['1','05','30'] to seconds."""
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0]) * 60 + int(parts[1])


def _parse_runs(text: str) -> list[dict]:
    """Parse distance + finish time from free text, e.g. '5.2km 25:30'."""
    runs: list[dict] = []
    for m in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(?:km|k)\b[^0-9]{0,25}?"
        r"(\d{1,3}):(\d{2})(?::(\d{2}))?", text, re.I,
    ):
        parts = [m.group(2), m.group(3)] + ([m.group(4)] if m.group(4) else [])
        runs.append({"dist_m": float(m.group(1)) * 1000.0,
                     "seconds": _parse_time(parts)})
    for m in re.finditer(
        r"(\d{3,5})\s*m\b[^0-9]{0,20}?"
        r"(\d{1,3}):(\d{2})(?::(\d{2}))?", text, re.I,
    ):
        parts = [m.group(2), m.group(3)] + ([m.group(4)] if m.group(4) else [])
        runs.append({"dist_m": float(m.group(1)),
                     "seconds": _parse_time(parts)})
    return runs


_LIFT_FWD = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z /()-]{1,35}?)\s+"
    r"(?P<sets>\d{1,2})\s*[x×*]\s*(?P<reps>\d{1,2})\s*"
    r"(?:@\s*)?(?P<weight>\d{1,4}(?:\.\d+)?)\s*(?P<unit>kg|lb)\b",
    re.I,
)
_LIFT_REV = re.compile(
    r"(?P<sets>\d{1,2})\s*[x×*]\s*(?P<reps>\d{1,2})\s*"
    r"(?:@\s*)?(?P<weight>\d{1,4}(?:\.\d+)?)\s*(?P<unit>kg|lb)\s+"
    r"(?P<name>[A-Za-z][A-Za-z /()-]{1,35})",
    re.I,
)


def _parse_lifts(text: str) -> list[dict]:
    """Parse named sets from free text; lb is converted to kg."""
    lifts: list[dict] = []
    for pattern in (_LIFT_FWD, _LIFT_REV):
        for m in pattern.finditer(text):
            name = re.sub(r"^(did|done|then|and)\s+", "", m.group("name"),
                          flags=re.I).strip(" .,-")
            if not name or name.lower() in {"kg", "lb", "reps"}:
                continue
            weight = float(m.group("weight"))
            if weight <= 0:
                continue
            if m.group("unit").lower() == "lb":
                weight *= 0.45359237
            lifts.append({"name": name, "sets": int(m.group("sets")),
                          "reps": int(m.group("reps")),
                          "weight": round(weight, 2)})
    return lifts


def _parse_niche(text: str) -> dict[str, float]:
    """Pull explicit niche metrics out of a Done cell."""
    out: dict[str, float] = {}
    m = re.search(r"broad\s+jump[^0-9]{0,20}(\d+(?:\.\d+)?)\s*cm", text, re.I)
    if m:
        out["BroadJump_cm"] = float(m.group(1))
    else:
        m = re.search(r"(?:vertical\s+)?jump[^0-9]{0,20}(\d+(?:\.\d+)?)\s*cm",
                      text, re.I)
        if m:
            out["Jump_cm"] = float(m.group(1))
    m = re.search(r"(?:10\s*m|10m)\s+sprint[^0-9]{0,10}"
                  r"(\d+(?:\.\d+)?)\s*s", text, re.I)
    if m:
        out["Sprint_10m_s"] = float(m.group(1))
    m = re.search(r"(?:mobility|dorsiflexion|sit\s*(?:and|&)\s*reach)"
                  r"[^0-9]{0,20}(\d+(?:\.\d+)?)\s*cm", text, re.I)
    if m:
        out["Mobility"] = float(m.group(1))
    return out


def _week_start(date_str: str) -> str:
    """Monday (ISO date) for a YYYY-MM-DD string."""
    d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")



def _sex_code(value: str) -> str:
    v = str(value).strip().lower()
    if v.startswith("m"):
        return "M"
    if v.startswith("f"):
        return "F"
    return ""


def _age_band(age: int) -> str:
    if age >= 80:
        return "80+"
    lo = (age // 5) * 5
    return f"{lo}-{lo + 4}"


def _weight_class(sex: str, weight: float) -> int:
    classes = ([53, 59, 66, 74, 83, 93, 105, 120, 999] if sex == "M"
               else [43, 47, 52, 57, 63, 69, 76, 84, 999])
    for upper in classes:
        if weight <= upper:
            return upper
    return classes[-1]


@functools.lru_cache(maxsize=1)
def _load_running_benchmarks() -> dict:
    index: dict[tuple[str, str, str], dict[int, float]] = {}
    try:
        with open(RUNNING_BENCH_FILE, newline="") as f:
            for row in csv.DictReader(f):
                key = (row["sex"], row["age_band"], row["event"])
                index.setdefault(key, {})[int(row["percentile"])] = float(
                    row["time_s"])
    except OSError:
        pass
    return index


@functools.lru_cache(maxsize=1)
def _load_lift_benchmarks() -> dict:
    index: dict[tuple[str, str, int, str], dict[int, float]] = {}
    try:
        with open(LIFT_BENCH_FILE, newline="") as f:
            for row in csv.DictReader(f):
                key = (row["sex"], row["lift"], int(row["weight_class_kg"]),
                       row["age"])
                index.setdefault(key, {})[int(row["percentile"])] = float(
                    row["kg"])
    except OSError:
        pass
    return index


def _lookup_running(sex: str, age: int, event: str) -> dict[int, float]:
    index = _load_running_benchmarks()
    for key in ((sex, _age_band(age), event), (sex, "all", event)):
        if key in index:
            return index[key]
    return {}


def _lookup_lift(sex: str, lift: str, weight: float, age: int) -> dict[int, float]:
    index = _load_lift_benchmarks()
    weight_class = _weight_class(sex, weight)
    for age_key in (str(age), "all"):
        key = (sex, lift, weight_class, age_key)
        if key in index:
            return index[key]
    return {}


def _cohort_bands(sex: str, birth_year: int, week_start: str,
                  weight: float) -> dict:
    out = {}
    if not sex or birth_year <= 0:
        return out
    age = max(15, min(90, int(week_start[:4]) - birth_year))
    for label, _col, kind, key in CHART_METRICS:
        data = {}
        if kind == "running":
            data = _lookup_running(sex, age, key)
        elif weight > 0:
            data = _lookup_lift(sex, key, weight, age)
        for pct in PERCENTILE_BANDS:
            out[f"{label}_P{pct}"] = data.get(pct, "")
    return out


def _chart_row(week: dict, sex: str, birth_year: int, weight: float) -> dict:
    row = {"Week_start": week["Week_start"]}
    for label, col, _kind, _key in CHART_METRICS:
        row[f"{label}_user"] = week.get(col, "")
    row.update(_cohort_bands(sex, birth_year, week["Week_start"], weight))
    return row


def _health_profile(sheets: Sheets) -> tuple[str, int, float]:
    history = sheets.rolling_history(TAB_HEALTH, 1)
    if not history:
        return "", 0, 0.0
    col = history[-1]["cols"][0]
    values = {field: (col[i] if i < len(col) else "")
              for i, field in enumerate(HEALTH_FIELDS)}
    sex = _sex_code(values.get("Gender", ""))
    birth_year = 0
    try:
        birth_year = int(float(values.get("Birth_year", "")))
    except (TypeError, ValueError):
        try:
            birth_year = datetime.now(timezone.utc).year - int(
                float(values.get("Age", "")))
        except (TypeError, ValueError):
            pass
    try:
        weight = float(values.get("Weight_kg", ""))
    except (TypeError, ValueError):
        weight = 0.0
    return sex, birth_year, weight


def write_chart_data(sheets: Sheets, weekly: dict) -> None:
    sex, birth_year, weight = _health_profile(sheets)
    for week in weekly.values():
        row_weight = week.get("Bodyweight_kg", "") or weight
        try:
            row_weight = float(row_weight)
        except (TypeError, ValueError):
            row_weight = weight
        chart = _chart_row(week, sex, birth_year, row_weight)
        sheets.upsert_table_row(TAB_CHARTS, 1, CHART_HEADERS, "Week_start",
                                chart, CHART_WEEKS)


def collapse_logs(sheets: Sheets) -> dict:
    """Collapse recent logs into weekly progression and summary data.

    Lifts become best weekly e1RM per benchmark, runs become best weekly
    flat/perfect-weather 5K and 400m equivalents, and explicit niche metrics
    become weekly bests. Raw text is kept only in the rolling UI window.
    """
    history = sheets.rolling_history(TAB_EXERCISE, 2)[-WINDOW_DAYS["exercise"]:]

    weight_by_date: dict[str, float] = {}
    for day in sheets.rolling_history(TAB_HEALTH, 1):
        col = day["cols"][0]
        idx = HEALTH_FIELDS.index("Weight_kg")
        if idx < len(col) and col[idx].strip():
            try:
                weight_by_date[day["date"]] = float(col[idx])
            except ValueError:
                pass

    weekly: dict[str, dict] = {}
    text_breakdowns: list[str] = []
    run_count = 0

    for day in history:
        rec = day["cols"][0] if day["cols"] else []
        done = day["cols"][1] if len(day["cols"]) > 1 else []
        date = day["date"]
        entry = weekly.setdefault(_week_start(date), {
            "lifts": [], "runs": [], "niche": {}, "groups": set(),
            "complete": 0, "bodyweight": [], "days": set(),
        })
        entry["days"].add(date)
        done_text = " | ".join(c for c in done if c.strip())
        rec_text = " | ".join(c for c in rec if c.strip())

        lifts = _parse_lifts(done_text)
        prescribed = False
        if not lifts:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|lb)\s+done\b",
                          done_text, re.I)
            rec_lifts = _parse_lifts(rec_text)
            if m and rec_lifts:
                weight = float(m.group(1))
                if m.group(2).lower() == "lb":
                    weight *= 0.45359237
                for lift in rec_lifts:
                    lift["weight"] = round(weight, 2)
                lifts = rec_lifts
            elif re.search(r"\bdone\b", done_text, re.I) and rec_lifts:
                lifts = rec_lifts
                prescribed = True

        for lift in lifts:
            benchmark = _benchmark_lift(lift["name"])
            if not benchmark or not (1 <= lift["reps"] <= 10):
                continue
            try:
                one_rm = (estimate_1rm(lift["weight"], lift["reps"], "epley")
                          / _lift_coefficient(lift["name"]))
            except (ValueError, TypeError):
                continue
            entry["lifts"].append(
                (benchmark, one_rm, date,
                 "prescribed" if prescribed else "logged"))
            entry["groups"].add(benchmark)

        for run in _parse_runs(done_text):
            seconds = run.get("seconds")
            if not seconds or run["dist_m"] <= 0:
                continue
            adj = calcpace_port.normalise_seconds(seconds, run["dist_m"])
            try:
                entry["runs"].append((
                    calcpace_port.riegel_predict(adj, run["dist_m"], 5000.0),
                    calcpace_port.riegel_predict(adj, run["dist_m"], 600.0),
                ))
                run_count += 1
            except ValueError:
                continue

        for key, value in _parse_niche(done_text).items():
            entry["niche"][key] = max(value, entry["niche"].get(key, value))
        if date in weight_by_date:
            entry["bodyweight"].append(weight_by_date[date])
        idx = EXERCISE_ROWS.index("Complete")
        if idx < len(done) and done[idx].strip().upper().startswith("Y"):
            entry["complete"] += 1
        for cell in done:
            if any(k in cell.lower() for k in ("plyo", "isometric", "sprint")):
                text_breakdowns.append(f"{date}: {cell.strip()[:120]}")

    weekly_out: dict[str, dict] = {}
    for ws, entry in sorted(weekly.items()):
        best: dict[str, tuple[float, str, str]] = {}
        for benchmark, one_rm, date, source in entry["lifts"]:
            if benchmark not in best or one_rm > best[benchmark][0]:
                best[benchmark] = (one_rm, date, source)
        row: dict = {
            "Week_start": ws,
            "n_lifts": len(entry["lifts"]),
            "n_runs": len(entry["runs"]),
            "5K_equiv_s": (round(min(r[0] for r in entry["runs"]))
                           if entry["runs"] else ""),
            "400m_equiv_s": (round(min(r[1] for r in entry["runs"]))
                             if entry["runs"] else ""),
            "Bodyweight_kg": (round(statistics.median(entry["bodyweight"]), 1)
                              if entry["bodyweight"] else ""),
            "Complete_days": entry["complete"],
            "n_days": len(entry["days"]),
            "groups": sorted(entry["groups"]),
        }
        for benchmark in BENCHMARK_LIFTS:
            value = best.get(benchmark)
            row[f"{benchmark}_e1RM"] = round(value[0], 1) if value else ""
        row.update(entry["niche"])
        sources = {source for _, _, source in best.values()}
        row["Source"] = ("mixed" if len(sources) > 1 else
                         next(iter(sources)) if sources else "")
        weekly_out[ws] = row

    lift_progression = []
    for benchmark in BENCHMARK_LIFTS:
        pts = [(ws, row[f"{benchmark}_e1RM"]) for ws, row in weekly_out.items()
               if row.get(f"{benchmark}_e1RM") != ""]
        if not pts:
            continue
        fit = _linear_fit(list(range(len(pts))), [p[1] for p in pts])
        if fit:
            lift_progression.append(
                f"{benchmark}: latest {pts[-1][1]:.1f}kg "
                f"({fit[0]:+.1f}kg/week over {len(pts)} weeks)")
        else:
            lift_progression.append(f"{benchmark}: latest {pts[-1][1]:.1f}kg")

    fitness = {}
    for label, key in (("aerobic_5k", "5K_equiv_s"),
                       ("anaerobic_400m", "400m_equiv_s")):
        pts = [row[key] for row in weekly_out.values() if row.get(key) != ""]
        if not pts:
            fitness[label] = f"{label}: no runs logged in window"
            continue
        fit = _linear_fit(list(range(len(pts))), pts)
        fitness[label] = (f"{label}: latest {pts[-1]:.0f}s"
                          + (f" ({fit[0]:+.1f}s/week)" if fit else ""))

    coeffs = _load_coefficients()
    hardy = _load_hardy()
    hardy_pct = _hardy_percentiles()
    hardy_lines = []
    for label, key in (("Squat", "squat"), ("Bench", "bench-press"),
                       ("Posterior", "deadlift")):
        p50 = hardy_pct.get(key, {}).get("e1rmP50Kg")
        p90 = hardy_pct.get(key, {}).get("e1rmP90Kg")
        if p50 is not None or p90 is not None:
            hardy_lines.append(f"{label}: P50 {p50 or '?'}kg, P90 {p90 or '?'}kg")
    overlay = ("Hardy: " + "; ".join(hardy_lines)
               if hardy_lines else
               f"Hardy benchmarks present: {len(hardy)} exercises")
    overlay += f"; Hevy coefficients: {len(coeffs)} groups"

    latest_niche = {}
    for _, row in sorted(weekly_out.items()):
        for key in ("Jump_cm", "BroadJump_cm", "Sprint_10m_s", "Mobility"):
            if row.get(key, "") != "":
                latest_niche[key] = row[key]

    return {
        "lift_progression": lift_progression or ["no lifts logged in window"],
        "hardy_overlay": overlay,
        "fitness_curves": fitness,
        "text_breakdowns": "; ".join(text_breakdowns[-5:]) or "none in window",
        "run_count": run_count,
        "weekly": weekly_out,
        "niche": latest_niche,
    }



# ---------------------------------------------------------------------------
# DeepSeek reasoning engine + Telegram delivery (README "Runtime")
# ---------------------------------------------------------------------------
# DeepSeek research tools: the only `tools` variant the API accepts is
# `function`. Backed by DeepSeek's own Search & Fetch API (same key, billed
# per successful request), so the model researches directly rather than via a
# separate PubMed round-trip. Capped per run to control cost (README "Web search").
RESEARCH_TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web. Each call is metered and billed, and the "
                       "budget is tight - search only to verify a specific, "
                       "evidence-critical fact or protocol; otherwise rely on "
                       "your own knowledge. One focused query per question. For "
                       "research-literature checks use the PubMed operator, e.g. "
                       "site:pubmed.ncbi.nlm.nih.gov. Returns ranked results "
                       "(title, url, summary, authority).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "Search query <=128 chars; supports site: / -site: operators"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_page",
        "description": "Fetch a URL and return its text as Markdown, to read a "
                       "specific page (e.g. a search result you need in full). "
                       "Each call is metered and billed - use sparingly.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "http(s) URL to fetch"},
        }, "required": ["url"]}}},
]


class DeepSeek:
    def __init__(self, settings: dict) -> None:
        self.api_key = settings["deepseek_api_key"]
        self.base_url = settings["deepseek_base_url"].rstrip("/")
        self.model = settings.get("deepseek_model", "deepseek-chat")
        self.max_tokens = settings.get("deepseek_max_tokens", DEEPSEEK_MAX_TOKENS)
        self.max_tool_rounds = settings.get("deepseek_max_tool_rounds",
                                            DEEPSEEK_MAX_TOOL_ROUNDS)
        self.search_budget = settings.get("research_search_budget",
                                          RESEARCH_SEARCH_BUDGET)
        self.fetch_budget = settings.get("research_fetch_budget",
                                         RESEARCH_FETCH_BUDGET)
        self._searches = 0
        self._fetches = 0
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required")


    def _api_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _run_tool(self, name: str, args: dict) -> dict:
        """Execute one function call from the model; budget-capped."""
        if name == "web_search":
            if self._searches >= self.search_budget:
                return {"error": "web_search budget exhausted; answer from your own knowledge."}
            query = str(args.get("query") or "").strip()[:128]
            if not query:
                return {"error": "missing query"}
            count = max(1, min(10, int(args.get("count") or 5)))
            try:
                body = {"query": query, "count": count, "need_content": False}
                r = requests.post(f"{self.base_url}/v1/tools/search",
                                  headers=self._api_headers(), json=body, timeout=60)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                return {"error": f"search failed: {e}"}
            self._searches += 1
            results = data.get("results", [])
            log.info("DeepSeek research: search %r -> %d results (%d/%d)",
                     query, len(results), self._searches, self.search_budget)
            return {"results": [{
                "title": x.get("title"), "url": x.get("url"),
                "summary": (x.get("summary") or "")[:800],
                "authority": x.get("authority"),
            } for x in results]}
        if name == "fetch_page":
            if self._fetches >= self.fetch_budget:
                return {"error": "fetch budget exhausted; answer from your own knowledge."}
            url = str(args.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                return {"error": "url must be http(s)"}
            try:
                r = requests.post(f"{self.base_url}/v1/tools/fetch",
                                  headers=self._api_headers(), json={"url": url}, timeout=60)
                r.raise_for_status()
                data = r.json()
            except requests.RequestException as e:
                return {"error": f"fetch failed: {e}"}
            self._fetches += 1
            log.info("DeepSeek research: fetch %s (%d/%d)",
                     url, self._fetches, self.fetch_budget)
            return {"title": data.get("title"),
                    "content": (data.get("content") or "")[:4000]}
        return {"error": f"unknown tool: {name}"}

    def chat(self, system: str, user: str, max_tokens: int | None = None,
             web_search: bool = False) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens or self.max_tokens,
        }
        if web_search:
            payload["tools"] = RESEARCH_TOOLS
        # Agentic research loop: the model decides when to search/fetch; tool
        # calls are machinery, not clarification round-trips (README step 5).
        for _round in range(self.max_tool_rounds):
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._api_headers(), json=payload, timeout=300,
            )
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return msg.get("content") or ""
            payload["messages"].append(msg)  # assistant message w/ tool_calls
            for tc in tool_calls:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self._run_tool(tc["function"]["name"], args)
                payload["messages"].append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False)[:4000],
                })
        raise RuntimeError(
            f"DeepSeek research loop exceeded {self.max_tool_rounds} rounds")


SYSTEM_PROMPT = (
    "You are a science-backed adaptive health and fitness program manager. "
    "You integrate gym training, running, nutrition, recovery and general "
    "health. Every decision derives from evidence with citations, never "
    "arbitrary hard-coded rules. Question assumptions and your own design; "
    "flag arbitrary decisions as such and offer alternatives. Do not "
    "regenerate prior standing advice without justification - cite why you "
    "change it. Follow a linear process: macro (strategy) -> meso (weekly "
    "schedule) -> micro (daily plan), with bidirectional feedback between "
    "layers. Be skeptical of weak sources; prefer meta-analyses, systematic "
    "reviews and RCTs. When a day lacks its completeness marker, treat its data "
    "as partial: use only what is present; never assume missing means zero. Web "
    "research is metered and the budget is tight - search only for specific, "
    "evidence-critical checks, never trivia. patterns_worthy must list only "
    "genuine, notable health/performance events (sickness, injury, drastic "
    "progression change, allergy, reported pain) - never routine data-entry "
    "states such as an empty Pantry or a run without logged lifts. Return ONLY "
    "a JSON object (no markdown fences) with these keys: diet_recommended, "
    "activities_recommended, diet_rows (object of Diet row label -> text), "
    "exercise_rows (object of Exercise row label -> text), exercise_prescription "
    "(array of {exercise, sets, reps, weight_kg, rpe, cue}), niche_trend (one "
    "line, e.g. 'vertical jump 100cm (Sep) -> 120cm (Nov)'), week_summary "
    "(object: Focus, Key_loads, Niche, Strategy; describe the trailing 7 days), "
    "weather_summary, patterns_worthy (array of strings), strategy_updates "
    "(array of {category, detail, citations, status}), longterm_review (string "
    "or empty)."
)


def extract_json(text: str) -> dict:
    """Robustly pull a JSON object out of a model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model reply: {text[:200]}")
    return json.loads(text[start:end + 1])


def send_telegram(text: str, settings: dict) -> bool:
    token, chat_id = settings["telegram_bot_token"], settings["telegram_chat_id"]
    if not token or not chat_id:
        log.warning("Telegram not configured; skipping notification.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]}, timeout=30,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Telegram send failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Context building (README "What is sent to DeepSeek each run")
# ---------------------------------------------------------------------------
def _render_history(history: list[dict], labels: list[str]) -> str:
    """Compact, human-readable window; history shows Done, today adds Recommended."""
    lines = []
    for i, day in enumerate(history):
        cols = day["cols"]
        done = cols[-1] if len(cols) > 1 else cols[0]
        complete_idx = labels.index("Complete")
        complete = (done[complete_idx].strip().upper()
                    if complete_idx < len(done) and done[complete_idx].strip()
                    else "?")
        parts = []
        for label, cell in zip(labels, done):
            if label == "Complete" or not cell.strip():
                continue
            parts.append(f"{label}={cell.strip()[:300]}")
        if i == len(history) - 1 and len(cols) > 1:
            rec = [f"{label}={cell.strip()[:300]}"
                   for label, cell in zip(labels, cols[0])
                   if label != "Complete" and cell.strip()]
            if rec:
                parts.append("Recommended: " + "; ".join(rec))
        lines.append(f"{day['date']} [{complete}] " + ("; ".join(parts) or "-"))
    return "\n".join(lines) or "no data"


def _progression_bests(rows: list[dict]) -> dict:
    """All-time best current values from the progression table."""
    out = {}
    for key in PROGRESSION_HEADERS:
        if key in ("Year", "Week", "n_lifts", "n_runs"):
            continue
        values = []
        for row in rows:
            try:
                values.append(float(row.get(key, "")))
            except (TypeError, ValueError):
                continue
        if not values:
            continue
        out[key] = min(values) if key in ("5K_equiv_s", "400m_equiv_s",
                                          "Sprint_10m_s") else max(values)
    return out


def build_context(sheets: Sheets, weather: str, today: str,
                  collapsed: dict) -> dict:
    health_history = sheets.rolling_history(TAB_HEALTH, 1)[-WINDOW_DAYS["health"]:]
    health = {}
    if health_history:
        col = health_history[-1]["cols"][0]
        for field, row in zip(HEALTH_FIELDS, col):
            health[field] = row

    progression = sheets.read_table(
        TAB_EXERCISE, PROGRESSION_HEADER_ROW, PROGRESSION_HEADERS,
        PROGRESSION_WEEKS)
    patterns = sheets.read_rows(TAB_PATTERNS, PATTERN_COLS)
    sex, birth_year, weight = _health_profile(sheets)
    week = (collapsed.get("weekly") or {}).get(_week_start(today), {})
    cohort = {}
    if week:
        chart_row = _chart_row(week, sex, birth_year, weight)
        for label, _col, _kind, _key in CHART_METRICS:
            cohort[label] = {
                "user": chart_row.get(f"{label}_user", ""),
                "p50": chart_row.get(f"{label}_P50", ""),
                "p90": chart_row.get(f"{label}_P90", ""),
            }

    return {
        "today": today,
        "strategy": sheets.read_rows(TAB_STRATEGY, STRATEGY_COLS),
        "pantry": sheets.read_rows(TAB_PANTRY,
                                   ["Ingredient", "Amount", "Expiry"]),
        "health_latest": health,
        "health_window": _render_history(health_history, HEALTH_FIELDS),
        "diet_window": _render_history(
            sheets.rolling_history(TAB_DIET, 2)[-WINDOW_DAYS["diet"]:], DIET_ROWS),
        "exercise_window": _render_history(
            sheets.rolling_history(TAB_EXERCISE, 2)[-WINDOW_DAYS["exercise"]:],
            EXERCISE_ROWS),
        "exercise_week_summary": sheets.read_table(
            TAB_EXERCISE, EXERCISE_SUMMARY_HEADER_ROW,
            EXERCISE_SUMMARY_HEADERS, WEEKLY_SUMMARY_WEEKS),
        "diet_week_summary": sheets.read_table(
            TAB_DIET, DIET_SUMMARY_HEADER_ROW,
            DIET_SUMMARY_HEADERS, WEEKLY_SUMMARY_WEEKS),
        "health_week_summary": sheets.read_table(
            TAB_HEALTH, HEALTH_SUMMARY_HEADER_ROW,
            HEALTH_SUMMARY_HEADERS, WEEKLY_SUMMARY_WEEKS),
        "progression_recent": progression[-WEEKLY_SUMMARY_WEEKS:],
        "progression_bests": _progression_bests(progression),
        "cohort_bands": cohort,
        "weather": weather,
        "collapsed": collapsed,
        "patterns_recent": [r for r in patterns if r.get("Status") != "archived"][-50:],
        "critical_incidents": [r["Detail"] for r in patterns
                               if r.get("Type") == "critical"
                               and r.get("Status") != "archived"],
    }



def update_patterns(sheets: Sheets, today: str, events: list[str]) -> None:
    """Append deduplicated significant events; the 4-week review archives them."""
    existing = {(r["Date"], r["Event"])
                for r in sheets.read_rows(TAB_PATTERNS, PATTERN_COLS)}
    for ev in events:
        if not ev or (today, ev) in existing:
            continue
        sheets.append_row(TAB_PATTERNS,
                          {"Date": today, "Event": ev, "Detail": ev,
                           "Type": "event", "Status": "new"},
                          PATTERN_COLS)



def archive_patterns(sheets: Sheets) -> None:
    """Mark reviewed patterns archived so the next 4-week window starts clean."""
    ws = sheets.ws(TAB_PATTERNS)
    values = ws.get_all_values()
    status_idx = PATTERN_COLS.index("Status") + 1
    for i, row in enumerate(values[1:], start=2):
        if len(row) >= status_idx and row[status_idx - 1].strip() != "archived":
            ws.update([["archived"]], f"{_col_letter(status_idx)}{i}")


def review_due(sheets: Sheets) -> bool:
    """Every 4 weeks a second message runs the long-term review (README)."""
    last = sheets.state_get("last_4week_review")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last_dt) >= timedelta(weeks=4)


# ---------------------------------------------------------------------------
# Write back (README step 6)
# ---------------------------------------------------------------------------
def _render_prescription(items: list[dict]) -> str:
    """Render the model's structured prescription as a parseable, readable line."""
    lines = []
    for item in items or []:
        name = str(item.get("exercise") or "").strip()
        if not name:
            continue
        sets, reps = item.get("sets"), item.get("reps")
        weight = item.get("weight_kg")
        line = name
        if sets and reps:
            line += f" {sets}x{reps}"
        if weight not in (None, ""):
            line += f"@{weight}kg"
        if item.get("rpe") not in (None, ""):
            line += f" RPE{item['rpe']}"
        if item.get("cue"):
            line += f" — {item['cue']}"
        lines.append(line)
    return "; ".join(lines)


def _health_week_stats(sheets: Sheets, week_start: str) -> dict:
    """Median/mean health values for the ISO week starting `week_start`."""
    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    end = start + timedelta(days=6)
    sleep, quality, weight = [], [], []
    cycle = ""
    for day in sheets.rolling_history(TAB_HEALTH, 1):
        try:
            day_date = datetime.strptime(day["date"][:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= day_date <= end):
            continue
        col = day["cols"][0]
        for field in ("Sleep_hours", "Sleep_quality", "Weight_kg"):
            idx = HEALTH_FIELDS.index(field)
            if idx >= len(col) or not col[idx].strip():
                continue
            try:
                value = float(col[idx])
            except ValueError:
                continue
            (sleep if field == "Sleep_hours" else
             quality if field == "Sleep_quality" else weight).append(value)
        idx = HEALTH_FIELDS.index("Cycle_day")
        if idx < len(col) and col[idx].strip():
            cycle = col[idx].strip()
    return {
        "Sleep_avg_h": round(sum(sleep) / len(sleep), 1) if sleep else "",
        "Sleep_median_h": round(statistics.median(sleep), 1) if sleep else "",
        "Sleep_quality_avg": round(sum(quality) / len(quality), 1) if quality else "",
        "Weight_kg": round(statistics.median(weight), 1) if weight else "",
        "Cycle_phase": cycle,
    }


def _write_values(sheets: Sheets, tab: str, block: dict, rows: list[str],
                  values: dict) -> None:
    """Write label -> text into a day block's Recommended column."""
    col = _col_letter(block["start"])
    ws = sheets.ws(tab)
    for label, text in values.items():
        if label in rows and str(text).strip():
            ws.update([[str(text)[:1000]]], f"{col}{rows.index(label) + 2}")



def write_back(sheets: Sheets, today: str, plan: dict, collapsed: dict) -> None:
    """Write structured recommendations, weekly summaries and progression rows."""
    diet_block = sheets.rolling_ensure_day(
        TAB_DIET, 2, today, DIET_ROWS, WINDOW_DAYS["diet"])
    ex_block = sheets.rolling_ensure_day(
        TAB_EXERCISE, 2, today, EXERCISE_ROWS, WINDOW_DAYS["exercise"])

    diet_rows = plan.get("diet_rows")
    if not isinstance(diet_rows, dict) or not diet_rows:
        diet_rows = {"Notes": plan.get("diet_recommended", "") or ""}
    _write_values(sheets, TAB_DIET, diet_block, DIET_ROWS, diet_rows)

    ex_rows = plan.get("exercise_rows")
    if not isinstance(ex_rows, dict):
        ex_rows = {}
    prescription = _render_prescription(plan.get("exercise_prescription") or [])
    if prescription:
        ex_rows.setdefault("Gym_session", prescription)
    if not ex_rows:
        ex_rows = {"Notes": plan.get("activities_recommended", "") or ""}
    _write_values(sheets, TAB_EXERCISE, ex_block, EXERCISE_ROWS, ex_rows)

    niche = str(plan.get("niche_trend") or "")[:500]
    sheets.ws(TAB_EXERCISE).update(
        [["Niche_trend", niche, today]], f"A{NICHE_ROW}:C{NICHE_ROW}")

    week_start = _week_start(today)
    week = (collapsed.get("weekly") or {}).get(week_start, {})
    summary = plan.get("week_summary")
    if not isinstance(summary, dict):
        summary = {}
    keys = [f"{benchmark}_e1RM" for benchmark in BENCHMARK_LIFTS]
    key_loads = "; ".join(
        f"{key.replace('_e1RM', '')} {week[key]}kg"
        for key in keys if week.get(key, "") != "")
    sheets.upsert_table_row(
        TAB_EXERCISE, EXERCISE_SUMMARY_HEADER_ROW, EXERCISE_SUMMARY_HEADERS,
        "Week_start", {
            "Week_start": week_start,
            "Focus": summary.get("Focus", ""),
            "Sessions_by_group": ", ".join(week.get("groups", [])),
            "Key_loads": key_loads,
            "Niche": summary.get("Niche", "") or niche,
            "Adherence": f"{week.get('Complete_days', 0)}/{week.get('n_days', 0)}",
            "Strategy": summary.get("Strategy", ""),
        }, WEEKLY_SUMMARY_WEEKS)

    diet_history = sheets.rolling_history(TAB_DIET, 2)[-WINDOW_DAYS["diet"]:]
    complete_idx = DIET_ROWS.index("Complete")
    diet_complete = sum(
        1 for day in diet_history
        if len(day["cols"]) > 1 and complete_idx < len(day["cols"][1])
        and day["cols"][1][complete_idx].strip().upper().startswith("Y"))
    diet_text = " ".join(" ".join(day["cols"][1]) for day in diet_history)
    calories = [int(x) for x in re.findall(r"(\d{3,4})\s*(?:kcal|cal)\b",
                                           diet_text, re.I)]
    protein = [int(x) for x in re.findall(r"(\d{1,3})\s*g\s*(?:protein|pro)\b",
                                          diet_text, re.I)]
    sheets.upsert_table_row(
        TAB_DIET, DIET_SUMMARY_HEADER_ROW, DIET_SUMMARY_HEADERS, "Week_start", {
            "Week_start": week_start,
            "Avg_calories": round(sum(calories) / len(calories)) if calories else "",
            "Protein_g": round(sum(protein) / len(protein)) if protein else "",
            "Adherence_pct": f"{diet_complete}/{len(diet_history)}",
            "Notes": summary.get("Strategy", ""),
        }, WEEKLY_SUMMARY_WEEKS)

    health_summary = _health_week_stats(sheets, week_start)
    health_summary.update({"Week_start": week_start,
                           "Notes": summary.get("Strategy", "")})
    sheets.upsert_table_row(
        TAB_HEALTH, HEALTH_SUMMARY_HEADER_ROW, HEALTH_SUMMARY_HEADERS,
        "Week_start", health_summary, WEEKLY_SUMMARY_WEEKS)

    for ws, row in (collapsed.get("weekly") or {}).items():
        iso = datetime.strptime(ws, "%Y-%m-%d").isocalendar()
        values = dict(row)
        values["Year"] = iso.year
        values["Week"] = f"{iso.week:02d}"
        values["Strategy"] = summary.get("Strategy", "") if ws == week_start else ""
        sheets.upsert_table_row(
            TAB_EXERCISE, PROGRESSION_HEADER_ROW, PROGRESSION_HEADERS,
            "Week_start", values, PROGRESSION_WEEKS)

    write_chart_data(sheets, collapsed.get("weekly") or {})
    log.info("Wrote back recommendations to Diet/Exercise for %s", today)



# ---------------------------------------------------------------------------
# Orchestrator (README "Daily pipeline")
# ---------------------------------------------------------------------------
def run() -> None:
    configure_logging()
    settings = load_settings()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sheets = Sheets(settings)

    # 1. Fetch weather.
    weather = fetch_weather(sheets, today)

    # 3. Advance repeating blocks (increment Cycle_day, carry forward fields).
    advance_repeating_blocks(sheets, today)

    # 4. Collapse logs (1RM + flat/perfect-weather runs, other activities apart).
    collapsed = collapse_logs(sheets)

    ds = DeepSeek(settings)
    ctx = build_context(sheets, weather, today, collapsed)
    due = review_due(sheets)

    # 5. Single-prompt DeepSeek run (one message; no clarification round-trip).
    prompt = (
        "Produce today's complete plan following macro -> meso -> micro, "
        "with bidirectional feedback. Research current evidence where needed "
        "(web search; keep calls within the budget). Cite sources; flag "
        "arbitrary decisions and offer alternatives. Constrain every decision "
        "by the Strategy constraints and desired body-fat goal; design Diet "
        "meals from the Pantry, prioritising items closest to expiry."
        + (REVIEW_PROMPT_TAIL if due else "")
        + "\n\nContext:\n"
        + json.dumps(ctx, ensure_ascii=False, indent=2)
    )
    try:
        log.info("DeepSeek context chars: %d", len(prompt))
        raw = ds.chat(SYSTEM_PROMPT, prompt, web_search=True)
    except requests.RequestException as e:
        log.error("DeepSeek run failed: %s", e)
        send_telegram(f"⚠️ trAIner error: {e}", settings)
        raise
    plan = extract_json(raw)

    # 6. Write back recommendations.
    write_back(sheets, today, plan, collapsed)

    # 7. Deliver the plan + weather summary to Telegram (one-way).
    message = (
        f"📅 {today}\n\n🌦 {weather}\n\n🗓 Plan\n{plan.get('activities_recommended', '')}"
        f"\n\n🍽 Diet\n{plan.get('diet_recommended', '')}"
    )
    send_telegram(message, settings)

    update_patterns(sheets, today, plan.get("patterns_worthy", []) or [])

    # Every 4 weeks: a second message for long-term decisions.
    if due:
        review = plan.get("longterm_review") or ""
        send_telegram(f"🧭 4-week review\n{review}", settings)
        existing = {r["Category"]: r for r in
                    sheets.read_rows(TAB_STRATEGY, STRATEGY_COLS)}
        for u in (plan.get("strategy_updates", []) or []):
            category = str(u.get("category", ""))
            if str(u.get("status", "")) == "removed" and category in existing:
                sheet = sheets.ws(TAB_STRATEGY)
                col_a = sheet.col_values(1)
                for i, c in enumerate(col_a, start=1):
                    if c == category:
                        sheet.delete_rows(i)
                        break
                continue
            sheets.append_row(TAB_STRATEGY, {
                "Category": category,
                "Detail": str(u.get("detail", "")),
                "Citations": str(u.get("citations", "")),
                "Status": str(u.get("status", "standing")),
                "Updated": today,
            }, STRATEGY_COLS)
        sheets.state_set("last_4week_review", datetime.now(timezone.utc).isoformat())
        archive_patterns(sheets)

    log.info("Daily pipeline complete for %s.", today)


if __name__ == "__main__":
    run()

