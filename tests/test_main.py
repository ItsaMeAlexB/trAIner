"""Small, dependency-free checks for the parsing and progression rules."""

import json
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class FakeSheets:
    def __init__(self, exercise, health=None):
        self.exercise = exercise
        self.health = health or []

    def rolling_history(self, name, width):
        return self.exercise if name == main.TAB_EXERCISE else self.health


class ParserTests(unittest.TestCase):
    def test_parse_lifts_forward(self):
        lifts = main._parse_lifts("Bench Press 3x6@60kg; Squat 3x5 @ 100 kg")
        self.assertEqual(lifts[0]["name"], "Bench Press")
        self.assertEqual(lifts[0]["weight"], 60.0)
        self.assertEqual(lifts[1]["name"], "Squat")
        self.assertEqual(lifts[1]["reps"], 5)

    def test_parse_lifts_reverse_and_pounds(self):
        lifts = main._parse_lifts("3x8@80lb RDL")
        self.assertEqual(len(lifts), 1)
        self.assertEqual(lifts[0]["name"], "RDL")
        self.assertAlmostEqual(lifts[0]["weight"], 36.29, places=1)

    def test_parse_runs_km_and_m(self):
        runs = main._parse_runs("5.2km 25:30; 400m 1:02")
        self.assertEqual(runs[0]["dist_m"], 5200.0)
        self.assertEqual(runs[0]["seconds"], 1530)
        self.assertEqual(runs[1]["dist_m"], 400.0)
        self.assertEqual(runs[1]["seconds"], 62)

    def test_parse_niche(self):
        niche = main._parse_niche(
            "vertical jump 120cm, mobility 10cm, 10m sprint 1.8s")
        self.assertEqual(niche["Jump_cm"], 120.0)
        self.assertEqual(niche["Mobility"], 10.0)
        self.assertEqual(niche["Sprint_10m_s"], 1.8)

    def test_benchmark_mapping(self):
        self.assertEqual(main._benchmark_lift("Bench Press"), "Bench")
        self.assertEqual(main._benchmark_lift("RDL"), "Posterior")
        self.assertEqual(main._benchmark_lift("Squat"), "Squat")
        self.assertEqual(main._benchmark_lift("Overhead Press"), "OHP")
        self.assertIsNone(main._benchmark_lift("Bicep Curl"))

    def test_week_start_is_monday(self):
        self.assertEqual(main._week_start("2026-11-03"), "2026-11-02")

    def test_render_prescription(self):
        text = main._render_prescription([{
            "exercise": "Bench Press", "sets": 3, "reps": 6,
            "weight_kg": 60, "rpe": 7, "cue": "elbows in"}])
        self.assertEqual(text, "Bench Press 3x6@60kg RPE7 — elbows in")

    def test_extract_json_with_fence(self):
        parsed = main.extract_json('```json\n{"a": 1}\n```')
        self.assertEqual(parsed, {"a": 1})


class ContextTests(unittest.TestCase):
    def test_render_history_marks_complete_and_only_today_recommended(self):
        labels = ["Gym_session", "Complete"]
        history = [
            {"date": "2026-11-01", "cols": [["old recommendation"], ["done"]]},
            {"date": "2026-11-02", "cols": [["Bench 3x6@60kg"], ["done", "Y"]]},
        ]
        text = main._render_history(history, labels)
        self.assertIn("2026-11-02 [Y]", text)
        self.assertIn("Recommended: Gym_session=Bench 3x6@60kg", text)
        self.assertNotIn("old recommendation", text)

    def test_progression_bests(self):
        rows = [
            {"Squat_e1RM": "100", "5K_equiv_s": "1200"},
            {"Squat_e1RM": "110", "5K_equiv_s": "1150"},
        ]
        best = main._progression_bests(rows)
        self.assertEqual(best["Squat_e1RM"], 110.0)
        self.assertEqual(best["5K_equiv_s"], 1150.0)


class CollapseTests(unittest.TestCase):
    def test_collapse_uses_weekly_best_and_prescribed_done(self):
        exercise = [
            {"date": "2026-11-02",
             "cols": [["Squat 3x5@100kg; Bench Press 3x6@60kg"],
                      ["Squat 3x5@100kg; Bench Press 3x6@60kg", "", "", "", "", "Y"]]},
            {"date": "2026-11-03",
             "cols": [["Squat 3x5@105kg"], ["done", "", "", "", "", "Y"]]},
        ]
        health = [{"date": "2026-11-02", "cols": [[""] * len(main.HEALTH_FIELDS)]}]
        collapsed = main.collapse_logs(FakeSheets(exercise, health))
        row = collapsed["weekly"]["2026-11-02"]
        self.assertGreater(row["Squat_e1RM"], 100)
        self.assertEqual(row["Complete_days"], 2)
        self.assertIn("Squat", row["groups"])
        self.assertEqual(row["Source"], "mixed")


class BenchmarkTests(unittest.TestCase):
    def test_age_band_and_weight_class(self):
        self.assertEqual(main._age_band(34), "30-34")
        self.assertEqual(main._age_band(82), "80+")
        self.assertEqual(main._weight_class("M", 80), 83)
        self.assertEqual(main._weight_class("F", 60), 63)

    def test_running_benchmark_lookup(self):
        bands = main._lookup_running("M", 30, "5000m")
        self.assertTrue(bands)
        self.assertIn(50, bands)

    def test_lift_benchmark_lookup(self):
        bands = main._lookup_lift("M", "squat", 80, 30)
        self.assertTrue(bands)
        self.assertIn(50, bands)

    def test_chart_row_has_user_and_bands(self):
        week = {"Week_start": "2026-11-02", "5K_equiv_s": 1500,
                "Squat_e1RM": 100, "Bodyweight_kg": 80}
        row = main._chart_row(week, "M", 1990, 80)
        self.assertEqual(row["5K_user"], 1500)
        self.assertEqual(row["Squat_user"], 100)
        self.assertNotEqual(row["Squat_P50"], "")
        self.assertNotEqual(row["5K_P50"], "")

    def test_hevy_coefficient(self):
        self.assertAlmostEqual(main._lift_coefficient("leg press"), 3.0)
        self.assertAlmostEqual(main._lift_coefficient("bench press"), 1.0)


if __name__ == "__main__":
    unittest.main()

