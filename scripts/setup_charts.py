"""Create the Dashboard line charts from the Charts data tab.

Run once after `setup_sheets.py` (or after a schema change). Daily runs only
write data; they never touch charts. Uses the existing service-account creds.
"""

from __future__ import annotations

import os
import sys

import requests
from google.auth.transport.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _grid(sheet_id: int, col: int) -> dict:
    return {
        "sheetId": sheet_id,
        "startRowIndex": 0,
        "endRowIndex": main.CHART_WEEKS + 1,
        "startColumnIndex": col,
        "endColumnIndex": col + 1,
    }


def _chart_request(charts_id: int, dashboard_id: int, metric: int,
                   anchor_row: int, anchor_col: int) -> dict:
    label = main.CHART_METRICS[metric][0]
    base = 1 + metric * (1 + len(main.PERCENTILE_BANDS))
    user_col = base
    band_cols = list(range(base + 1,
                           base + 1 + len(main.PERCENTILE_BANDS)))
    series = [{
        "series": {"sourceRange": {"sources": [_grid(charts_id, user_col)]}},
        "targetAxis": "LEFT_AXIS",
    }]
    series += [{
        "series": {"sourceRange": {"sources": [_grid(charts_id, col)]}},
        "targetAxis": "LEFT_AXIS",
    } for col in band_cols]
    return {"addChart": {"chart": {
        "spec": {
            "title": f"{label} — user vs cohort",
            "basicChart": {
                "chartType": "LINE",
                "headerCount": 1,
                "legendPosition": "BOTTOM_LEGEND",
                "domains": [{"domain": {"sourceRange": {
                    "sources": [_grid(charts_id, 0)]}}}],
                "series": series,
            },
        },
        "position": {"overlayPosition": {
            "anchorCell": {
                "sheetId": dashboard_id,
                "rowIndex": anchor_row,
                "columnIndex": anchor_col,
            },
            "widthPixels": 720,
            "heightPixels": 360,
        }},
    }}}


def main_run() -> None:
    sheets = main.Sheets(main.load_settings())
    workbook = sheets.workbook
    charts = workbook.worksheet(main.TAB_CHARTS)
    dashboard = workbook.worksheet(main.TAB_DASHBOARD)
    charts_id, dashboard_id = charts.id, dashboard.id

    creds = sheets.creds
    if not creds.valid:
        creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/"
           f"{workbook.id}:batchUpdate")

    # Remove existing charts anchored on Dashboard so this is rerunnable.
    meta = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{workbook.id}",
        headers=headers, params={"fields": "sheets(properties(sheetId),charts(chartId,position))"},
        timeout=30).json()
    delete_requests = []
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") != dashboard_id:
            continue
        for chart in sheet.get("charts", []):
            delete_requests.append({"deleteEmbeddedObject": {
                "objectId": chart["chartId"]}})
    if delete_requests:
        requests.post(url, headers=headers,
                      json={"requests": delete_requests}, timeout=30).raise_for_status()

    positions = [(0, 0), (0, 9), (18, 0), (18, 9), (36, 0), (36, 9), (54, 0)]
    reqs = [_chart_request(charts_id, dashboard_id, i, r, c)
            for i, (r, c) in enumerate(positions)]
    r = requests.post(url, headers=headers, json={"requests": reqs}, timeout=60)
    r.raise_for_status()
    print(f"created {len(reqs)} charts on {main.TAB_DASHBOARD}")


if __name__ == "__main__":
    main_run()
