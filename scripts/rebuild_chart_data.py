"""One-time backfill of the Charts tab from the progression table."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def run() -> None:
    sheets = main.Sheets(main.load_settings())
    rows = sheets.read_table(main.TAB_EXERCISE, main.PROGRESSION_HEADER_ROW,
                             main.PROGRESSION_HEADERS, main.PROGRESSION_WEEKS)
    weekly = {row["Week_start"]: row for row in rows if row.get("Week_start")}
    main.write_chart_data(sheets, weekly)
    print(f"backfilled {len(weekly)} chart rows")


if __name__ == "__main__":
    run()
