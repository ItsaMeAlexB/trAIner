"""One-time setup: create/repair the 6 README tabs.

Usage:  python setup_sheets.py
Reads the same credentials as main.py (GOOGLE_SHEETS_JSON / GOOGLE_SHEETS_KEY,
or a local service-account JSON with an embedded SHEET_ID).

Creates the six tabs described in the README "Sheets schema":
  Strategy, Health, Pantry, Diet, Exercise, Patterns

Diet and Exercise are three-columns-per-day rolling tabs
(Recommended | Done | Complete); Health is a one-column-per-day rolling tab.
Row labels (fields) are pre-filled. Stale tabs left over from an older schema
are deleted only if they are empty. This script is idempotent.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (  # noqa: E402
    TABS, TAB_STRATEGY, TAB_HEALTH, TAB_PANTRY, TAB_DIET, TAB_EXERCISE,
    TAB_PATTERNS, STRATEGY_COLS, HEALTH_FIELDS, DIET_ROWS, EXERCISE_ROWS,
    PATTERN_COLS, EXERCISE_SUMMARY_HEADERS, DIET_SUMMARY_HEADERS,
    HEALTH_SUMMARY_HEADERS, PROGRESSION_HEADERS, EXERCISE_SUMMARY_HEADER_ROW,
    DIET_SUMMARY_HEADER_ROW, HEALTH_SUMMARY_HEADER_ROW, PROGRESSION_HEADER_ROW,
    NICHE_ROW, load_dotenv,
)

# README sheet schema: Ingredient, Amount, Expiry. Storage location is not
# stored (assumed from the ingredient); neither Quantity nor Shopping List.
PANTRY_COLS = ["Ingredient", "Amount", "Expiry"]


def connect():
    load_dotenv()
    key = os.environ.get("GOOGLE_SHEETS_KEY")
    raw = os.environ.get("GOOGLE_SHEETS_JSON")
    if not raw:
        for f in os.listdir("."):
            if not f.endswith(".json"):
                continue
            try:
                d = json.load(open(f))
                if "private_key" in d and "client_email" in d:
                    raw = json.dumps(d)
                    key = key or d.get("SHEET_ID")
                    break
            except json.JSONDecodeError:
                continue
    if not raw or not key:
        raise SystemExit("No credentials found (set GOOGLE_SHEETS_JSON/KEY).")

    info = json.loads(raw)
    info.pop("SHEET_ID", None)
    from google.oauth2 import service_account
    import gspread

    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds).open_by_key(key), key


def _data_rows(ws) -> int:
    """Count non-header rows with any content (safety guard before deletion)."""
    n = 0
    for r in ws.get_all_values()[1:]:
        if any(cell.strip() for cell in r):
            n += 1
    return n


def _label_column(ws, labels: list[str]) -> None:
    """Write A1='Day' and the row labels down column A."""
    if (ws.row_values(1) or [""])[0] != "Day":
        ws.update([["Day"]], "A1")
    for i, lab in enumerate(labels, start=2):
        ws.update([[lab]], f"A{i}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Create/repair trAIner sheets")
    parser.add_argument("--rebuild", action="store_true",
                        help="delete and recreate the six tabs (refuses non-empty tabs)")
    parser.add_argument("--force", action="store_true",
                        help="allow --rebuild to delete tabs that contain data")
    parser.add_argument("--reset-rolling", action="store_true",
                        help="rename Health/Diet/Exercise to *_backup_<date> and "
                             "recreate them with the current schema; keeps "
                             "Strategy/Pantry/Patterns")
    args = parser.parse_args()

    workbook, key = connect()
    existing = {w.title: w for w in workbook.worksheets()}

    if args.reset_rolling:
        stamp = datetime.now().strftime("%Y%m%d")
        for title in (TAB_HEALTH, TAB_DIET, TAB_EXERCISE):
            ws = existing.get(title)
            if ws is None:
                continue
            backup = f"{title}_backup_{stamp}"
            ws.update_title(backup)
            existing.pop(title, None)
            existing[backup] = ws
            print(f"backed up {title} -> {backup}")


    if args.rebuild:
        for title in TABS:
            ws = existing.get(title)
            if ws is not None and _data_rows(ws) and not args.force:
                raise SystemExit(
                    f"Refusing to rebuild: tab {title!r} has data. "
                    "Re-run with --force after backing up.")
        for title in list(TABS):
            if title in existing:
                workbook.del_worksheet(existing[title])
                print(f"deleted for rebuild: {title}")
        existing = {}

    # Tabs we no longer use (older schema). Delete only if empty.
    STALE = (TABS + [
        "Goals", "State", "ExerciseDB", "Ingredients", "Recipes",
        "DietLog", "GymLog", "RunLog", "ExerciseLog", "Routines",
    ])
    for title in list(existing):
        if title in TABS:
            continue
        ws = existing[title]
        if _data_rows(ws) == 0:
            workbook.del_worksheet(ws)
            print(f"deleted empty stale tab: {title}")
        else:
            print(f"KEEP non-empty tab {title!r} (not part of README schema)")

    # Re-read existing after deletion.
    existing = {w.title: w for w in workbook.worksheets()}

    def _header(ws, row: int, headers: list[str]) -> None:
        ws.update([headers], f"A{row}")

    def ensure(title, create):
        if title in existing:
            ws = existing[title]
            action = "verified"
        else:
            ws = workbook.add_worksheet(title=title, rows=1000, cols=100)
            action = "created"
        create(ws)  # idempotent: writes header row / label column A
        print(f"{action} sheet: {title}")

    def _pantry(ws):
        ws.update([PANTRY_COLS], "A1")

    def _patterns(ws):
        ws.update([PATTERN_COLS], "A1")

    def _health(ws):
        _label_column(ws, HEALTH_FIELDS)
        _header(ws, HEALTH_SUMMARY_HEADER_ROW, HEALTH_SUMMARY_HEADERS)

    def _diet(ws):
        _label_column(ws, DIET_ROWS)
        _header(ws, DIET_SUMMARY_HEADER_ROW, DIET_SUMMARY_HEADERS)

    def _exercise(ws):
        _label_column(ws, EXERCISE_ROWS)
        ws.update([["Niche_trend", "", ""]], f"A{NICHE_ROW}")
        _header(ws, EXERCISE_SUMMARY_HEADER_ROW, EXERCISE_SUMMARY_HEADERS)
        _header(ws, PROGRESSION_HEADER_ROW, PROGRESSION_HEADERS)

    ensure(TAB_STRATEGY, lambda ws: ws.update([STRATEGY_COLS], "A1"))
    ensure(TAB_PANTRY, _pantry)
    ensure(TAB_PATTERNS, _patterns)
    ensure(TAB_HEALTH, _health)
    ensure(TAB_DIET, _diet)
    ensure(TAB_EXERCISE, _exercise)

    print(f"\nDone. Sheet id: {key}")
    print("Tabs:", [w.title for w in workbook.worksheets()])



if __name__ == "__main__":
    main()
