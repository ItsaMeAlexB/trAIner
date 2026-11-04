# trAIner — Daily Fitness & Health Program Manager

A **daily, science-backed, adaptive health and fitness program manager** that integrates gym training, running, nutrition, recovery, and general health into a single holistic pipeline. **DeepSeek** is the reasoning engine — it interprets messy data, researches current evidence, and generates personalised plans. External tools and APIs are used only where they add mathematical precision or factual grounding; DeepSeek orchestrates everything and retains final decision-making authority.

*This system does not follow a fixed template. It embraces real-world messiness, adapts continuously to new data, and uses an LLM to research and reason from current science. Every decision is subject to questioning — macro, micro, and the very design itself, including what data can be requested and what can be asked to train. Every assumption can be challenged. The architecture is dynamic by design.*

## Core principles

- **No arbitrary hardcoded rules** — every decision is derived from DeepSeek's interpretation of data and current science.
- **Continuous evolution** — plans adapt based on progress, feedback, and new research.
- **Cross-domain integration** — gym, running, nutrition, health, menstrual cycle, and routines are interconnected.
- **Citations and skepticism** — all advice is backed by sources, respecting the hierarchy of evidence, and active questioning of assumptions. Proven science to effectively achieve goals is favoured over trends and unproven science.
- **DeepSeek is not a yes-man** — it must challenge unsupported preferences and research suggestions before implementing.
- **Consistency where beneficial** — DeepSeek must not regenerate prior advice without justification. If changing a standing recommendation, it cites why. This prevents arbitrary churn in the name of flexibility.

## Design principle: separation of concerns

**DeepSeek does the language work. APIs only answer factual questions.** Any service whose sole job is NLP parsing is redundant — DeepSeek natively parses the user's natural-language input ("5.6k run, 2×500m intervals at 4:30/km, rest at 5:30, hilly paved") into structured data, then queries the appropriate database for facts (macros, exercise metadata, percentiles, weather).

This extends to shorthand food logging ("same as yesterday without apple") — native DeepSeek understanding, no dedicated parser needed.

## Runtime

- **Host:** GitHub Actions today; planned move to a VPS (see VPS deployment).
- **Schedule:** GitHub Actions at 05:00 UTC. VPS target: **06:15 Europe/Berlin daily** (05:15 UTC in winter, 04:15 UTC in summer), inside DeepSeek off-peak and clear of the 06:30 job.
- **Data store:** Google Sheets (8 tabs: 6 data tabs + `Charts` data tab + `Dashboard`).
- **UI:** Telegram — **one-way only.** No user input via Telegram. No message history sent to DeepSeek. Telegram receives the day's plan and weather summary.
- **DeepSeek API:** Official host (`https://api.deepseek.com`).
- **No dry-run mode.**

## VPS deployment (planned)

The local directory remains the test environment. The unit files are committed under `deploy/`; install them on the VPS:

```bash
sudo cp deploy/trainer.service deploy/trainer.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trainer.timer
systemctl list-timers --all
```

```ini
# deploy/trainer.service
[Service]
Type=oneshot
User=alex
WorkingDirectory=/opt/trAIner
EnvironmentFile=/opt/trAIner/.env
ExecStart=/opt/trAIner/.venv/bin/python main.py
TimeoutStartSec=1800
```

```ini
# deploy/trainer.timer
[Timer]
OnCalendar=*-*-* 06:15:00 Europe/Berlin
Persistent=true
```

`Persistent=true` catches up after downtime. The target is 06:15 German time (05:15 UTC winter / 04:15 UTC summer), inside DeepSeek off-peak and clear of the 06:30 job.


## Daily pipeline

Each run, in order:

1. **Fetch weather** — Open-Meteo for the user's location (hourly forecast, up to 16 days).
2. **Load rolling windows** — Exercise 14 days, Diet 7 days, Health 35 days, plus Pantry and current Strategy. Completeness is a row in each day block. Incomplete days are treated as partial: use only present data; do not infer missing values as zero/default; do not adjust plans as if the day were fully logged unless the day is marked complete.
3. **Advance repeating blocks** — increment `Cycle_day`; DeepSeek-managed repeating blocks (menstrual cycle, retinol, dermarolling) advance or reset as defined.
4. **Collapse logs** — all logged lifts converted to estimated 1RM via `strength-utils` and mapped to five stable benchmarks (Squat, Bench, Posterior chain, Overhead Press, Pull); exercise-to-exercise conversions via Hevy coefficients; runs converted to flat/perfect-weather 5K and 400m equivalents via local `calcpace_port`; **other activities (hiking, swimming, cycling, etc.) are not converted to running equivalents** — they are logged separately and used only for short-term recovery/load and calorie accounting. Weekly bests update the two-year progression table; niche metrics (jump, sprint, mobility) are stored as weekly bests, with a one-line narrative refreshed daily.
5. **DeepSeek single-prompt run** — one message per run. No clarification round-trip. The prompt contains everything needed to make decisions. Follows linear **macro → meso → micro** with bidirectional feedback. Web search enabled for practical queries and specific research checks.
6. **Write back** — DeepSeek writes the prescription into the **Recommended** column of each day block (Diet, Exercise); the **Done** column is filled by the user. Each day is two columns plus a `Complete` row. Structured prescriptions are rendered in a parseable form (`Bench Press 3x6@60kg RPE7 — elbows in`), and the run also upserts the current week's summary and progression rows.
7. **Deliver to Telegram** — the day's plan, weather summary, and recommendations (e.g., "eat more carbs today in prep for tomorrow", "will rain 2–10pm", "drink more water").

**Every 4 weeks, a second message is sent** for long-term decisions (see Patterns & 4-Week Review).

## What is sent to DeepSeek each run

**Daily:**
- Exercise: 14 days of `Done` + `Complete`, plus today's `Recommended`. History sends only what was actually done; old recommendations are noise.
- Diet: 7 days of `Done` + `Complete`, plus today's `Recommended`.
- Health: 35 days compressed to non-empty fields (cycle context), with 14-day sleep detail.
- 12 weekly summaries per domain (the meso layer).
- Last 12 weeks of the progression table plus all-time bests and the niche narrative.
- Current Strategy, Pantry, today's weather.
- Patterns since the last review, plus permanent critical incidents.
- Completeness markers for every day. Incomplete days are partial data; missing entries are not zero/default.

**Every 4 weeks (additional):**
- 52 weekly summaries and the full 104-week progression table.
- Patterns and Strategy, with no raw daily logs (except the last 7 days for continuity).
- Long-term review and Strategy mutations.

## Sheets schema (8 tabs)

| Sheet | Purpose |
|---|---|
| **Strategy** | Goals, constraints, preferences, validated patterns, standing decisions, **pending design questions awaiting confirmation**, **desired body-fat percentage goal**, **dietary pattern and meal preferences**. E.g., "get stronger but ≤5 hrs gym/week", veganism, food allergies, sleep issues after certain ingredients, good reactions to specific exercises, bulk-prep preference. |
| **Health** | One column per day, 35-day window. Sleep (length + quality, or free text), menstrual cycle (`Last_cycle_start`, `Cycle_length_avg`, `Cycle_day`), **age, birth year, gender**, bodyweight, height, location, injuries, medical events, retinol and dermarolling routines. **Each daily entry includes a `Complete` row.** Plus a 12-week Health summary block. |
| **Pantry** | Ingredient, **Amount**, Expiry (frozen if applicable). Storage location is not stored; it is assumed from the ingredient (e.g., chicken goes in the fridge). |
| **Diet** | **Two columns per day:** **Recommended** (target calories/macros/micros; meals composed from pantry, prioritising items closest to expiry) \| **Done** (what was actually eaten, with water). A `Complete` row inside each day block is the completeness marker. 7-day raw window, plus a 12-week Diet summary block. Supplements tracked here. |
| **Exercise** | **Two columns per day:** **Recommended** (exercises with cues, sourced from wger and DeepSeek research) \| **Done** (`done`, `failed on 4th rep`, `felt light, 3 RIR`, free-text runs and other activities). A `Complete` row is the marker. 14-day raw window. Below it: a 12-week Exercise summary, a one-line `Niche_trend` narrative, and the **104-week progression table** (Squat, Bench, Posterior chain, OHP, Pull, 5K, 400m, jump, sprint, mobility, strategy annotation). Other activities are logged but not collapsed into long-term progression. |
| **Patterns** | Deduplicated significant events with a `Status` (`new` / `archived`). Sent to DeepSeek every 4 weeks; the daily run decides what is worthy (sickness, low iron, drastic progression change, reported pain). Archived by the review so the next window starts clean. |
| **Charts** | Machine-managed chart data: one row per week, user values plus p10–p90 cohort bands for 5K, 400m, Squat, Bench, Posterior, OHP and Pull. Charts reference this tab. |
| **Dashboard** | Human-facing line charts: user progression versus age/sex/weight-class percentile bands. Created once by `scripts/setup_charts.py`. |

## Data retention and windows

- **Tiered raw windows:** Exercise 14 days, Diet 7 days, Health 35 days. Raw detail is not kept for a year; the progression table is the long-term memory.
- **Weekly summaries:** 12 weeks per domain, one row per ISO week. Exercise summary captures focus, sessions by group, key loads, niche work and adherence; Diet captures averages/adherence; Health captures sleep, weight and cycle phase.
- **Overwrite mechanism:** when a new day arrives, delete the oldest day block, shift left, append the new day. **Genuinely deleted.** Summary/progression tables roll the same way after 12 weeks / 104 weeks.
- **Long-term progression:** one row per ISO week for two years, grouped by `Year`. Strength stores the weekly best e1RM per benchmark; running stores the weekly fastest normalised 5K and 400m equivalents; bodyweight/sleep use weekly medians. `Source` marks `logged`, `prescribed` (a bare `done`) or `mixed`. The `Strategy` column is the weekly macro annotation (e.g. "5 gym days, no running, deficit, poor sleep").
- **Niche work:** jump, sprint and mobility metrics are stored as weekly bests when measured. A single `Niche_trend` cell is overwritten daily with a one-line narrative (e.g. "vertical jump 100cm (Sep) → 120cm (Nov)"), so it does not swamp the sheet.
- **Repeating blocks:** monthly items (menstrual cycle, retinol, dermarolling) create a new block on overwrite rather than shifting out. DeepSeek defines block length for cycle-driven routines; user defines arbitrary ones (e.g., rest days).
- **Critical incidents** (injuries, allergies, severe sickness) are permanently flagged and always injected into the prompt, outside the raw window.
- **Completeness markers:** the `Complete` row records `Y`, `N`, or blank. Incomplete days are partial data; absence of data is not evidence of zero/default.

## Patterns & 4-week review

- **Storage:** deduplicated significant events with `Status` (`new` / `archived`); no silent 2-day rolling.
- **Cadence:** sent to DeepSeek every 4 weeks.
- **Entry decision:** the daily run decides what is worthy (sickness, low iron, drastic progression change, reported pain).
- **4-week review:** a second Telegram message is sent for long-term decisions. The review:
  - Revisits Strategy (new progression strategies, new diet plans).
  - Reviews 52 weekly summaries and the full progression table, not raw daily logs.
  - Archives reviewed Patterns and promotes legitimate ones to Strategy.
  - Removes stale entries from Strategy and Health.
  - Can replan the menstrual cycle and the benchmark/niche metric set.
  - Uses web search and PubMed to check strategies against current evidence.
- **Validation:** legitimate patterns are stored in Strategy, guiding future diet/activities. Validated or negated by future findings.

## Design changes require confirmation

DeepSeek is instructed to question everything, including the design itself (e.g., "Is there a reason this sheet exists? Should it be merged?"). However:

- **Design changes require user confirmation before implementation.**
- Telegram notifies of pending questions; the question is stored in **Strategy** and remains until answered.
- **No changes are made until the question is answered.**
- Once answered, the resolution remains in Strategy as a standing decision.

## Calculators and conversions

### Lifting

| Task | Tool |
|---|---|
| 1RM from any rep range | `strength-utils` (Epley, Brzycki, Lombardi, Mayhew) |
| Exercise-to-exercise conversion | Hevy Ranks `GROUP_COEFFS` (local JSON, extracted from GitHub) |
| Fallback for unlisted exercises | wger `variations` array |
| Rep-range ↔ weight (4RM → 10RM, etc.) | `strength-utils` `predict_weight_for_reps` |
| External strength comparison | Fitness Volt API (verified + gym populations, never blended) |
| Progression benchmarks | Hardy CSV (overlay on graphs) |

**Info loss:** when a lift is collapsed to 1RM, tempo, paused reps, RIR, and isometric details are lost from the graph view but preserved in the raw Exercise log for the duration of the window.

**Muscle group awareness:** wger returns `muscles_primary` and `muscles_secondary` per exercise at runtime. No local muscle file needed. DeepSeek uses this to avoid overloading recently-trained muscle groups.

### Running

Running is a primary fitness modality. Lifting and running are the two modalities with long-term progression tracking. Other activities are treated separately.

| Task | Tool |
|---|---|
| Benchmark data | Normalised NRCD (5K aerobic, 400m/800m anaerobic, flat/perfect-weather) |
| Grading to flat/perfect weather | `calcpace_port.py` (Minetti gradient, El Helou/Ely heat, NCAA altitude) |
| VDOT, paces, race predictions | `calcpace_port.py` (Riegel, Daniels) |
| Today's weather | Open-Meteo API |

**Two fitness curves:** 5K-equivalent (aerobic) and 400m-equivalent (anaerobic), normalised to flat/perfect weather. Weekly fastest values feed the progression table and charts.

**No cross-modality conversion:** cycling, swimming, walking, hiking, stair, etc. are **not** converted to running equivalents. They are logged as separate activities, with estimated calories and inferred muscle-group involvement, and used only for short-window recovery/load and calorie accounting.

**Elevation input:** inferred from the right column's free-text run description (e.g., "hilly paved") or entered manually. No GPX parsing.

**Progressions:** weekly best e1RM / normalised run time rows in the two-year table; the last 12 weeks and all-time bests are sent daily, the full table at the 4-week review. Overlaid with benchmark CSVs.

**Text breakdowns:** plyometrics, isometrics, sprints, etc. are kept as recent Done text and as weekly niche bests; a one-line `Niche_trend` narrative is sent daily.

### Other activities

Non-lifting and non-running activities are just treated separately — something a user might do and mark.

- **Calories burned** are estimated from activity type, duration, intensity, bodyweight, and cited MET/compendium values where available.
- **Muscle-group involvement** is inferred where relevant (e.g., hiking = quads/glutes, swimming = shoulders/lats, cycling = quads/calves). DeepSeek may take this into consideration when designing following days' workouts/recovery.
- **Long-term storage:** these activities do **not** stay in long-term storage like lifting or running. Lifting graphs onto benchmark lifts for all muscle groups; running graphs onto anaerobic/aerobic benchmark runs. Other activities remain in the rolling window and may inform short-term load, calories, and recovery only.

### Diet and nutrition

- **Maintenance calories:** DeepSeek calculates maintenance calories from Health data (age, gender, bodyweight, height, activity), planned/logged activities, and bodyweight trend. It uses the **desired body-fat percentage goal in Strategy** to design a sustainable deficit/surplus to reach that goal. Calories burned through activities are included.
- **Macros and micros:** macros must meet requirements. If resistance training is high, protein must be high. Ensure fibre, essential fats, creatine, omega-3, and other relevant micronutrients.
- **Dietary pattern:** check Strategy — could say vegan, vegetarian, omnivore, allergies, bulk-prep preference, quick meals, etc.
- **Vegan/vegetarian considerations:** recommend B12 if not already taking it, complementary proteins for optimal amino acid profile, vitamin D in northern climates in winter, iron if high risk of anaemia. For iron, use plant-based iron + vitamin C if vegan/vegetarian; recommend alternatives such as liver only where compatible with dietary constraints and safe.
- **Meal design:** firstly use expendable meals for designing the day's food — i.e., prioritise pantry items/meals closest to expiry. If low, use expendable ingredients to design meals. If limited, recommend new pantry ingredients to add.
- **Water:** water can also be tracked in the Diet sheet.
- **Supplements:** tracked in Diet.
- **Feedback:** adjust based on performance, recovery, satiety, GI comfort, and adherence.

## External APIs (factual lookup only)

| Task | Source | Auth | Notes |
|---|---|---|---|
| Food macros (generic/whole foods) | **USDA FoodData Central** | Free key | 1,000 req/hr personal key |
| Exercise metadata | **wger.de** | None | Public endpoints; `muscles_primary`, `muscles_secondary`, `variations` array |
| Scientific research | **PubMed E-utilities** | None | 3 req/s (10/s with free key) |
| External strength standards | **Fitness Volt Strength Standards API** | None | Verified + Gym populations |
| Weather | **Open-Meteo** | None | 16-day hourly forecast, historical data, ECMWF model |

## Offline datasets (downloaded once, committed to repo)

| Dataset | Source | License | Purpose |
|---|---|---|---|
| Hardy Strength Progression Report 2026 | hardy.app | CC BY 4.0 | Lifting progression benchmarks: time to plate milestones, 1RM gains by month. Does not contain bodyweight/age/sex. |
| Hevy exercise coefficients | `BenjiPy/hevy-ranks` `src/engine.js` | MIT | Exercise-to-exercise conversion ratios; generated by `scripts/build_exercise_coefficients.py`. |
| Italian Athletics historical bests | Kaggle `davideliu/italian-athletics-historical-best-performances` | CC0 | 900k+ age/sex running results, 2005–2021, including 400m/800m/5000m/10000m; source for running percentile bands. |
| LiftGauge strength cubes | `arvidfcjarfalla-prog/liftgauge-strength-data` | CC0 (data), MIT (code) | Pre-aggregated OpenPowerlifting S/B/D percentiles by sex × bodyweight class × single-year age × equipment. |
| OpenPowerlifting bulk CSV | openpowerlifting.org | CC0 | Raw fallback for rebuilding lift percentiles; not committed. |
| NRCD | GitHub v2.1.0 | CC BY 4.0 | 143,868 college running results; gender/grade, no age. Secondary running comparison. |
| Fitness Volt `fvss/v1` | fitnessvolt.com | CC BY 4.0 | Optional OHP/Pull/Row standards; only used if the endpoint is reachable. |

Generated benchmark files (committed):

| File | Purpose |
|---|---|
| `data/benchmarks/running_percentiles.csv` | Sex × 5-year age band × event × p10–p90 time, with n. |
| `data/benchmarks/lift_percentiles.csv` | Sex × lift × IPF weight class × age × p10–p90 kg, with n. |
| `data/exercise_coefficients.json` | Hevy `GROUP_COEFFS` extracted as JSON. |

**Maintenance note:** rebuild scripts are provided for when a source dataset updates; regeneration is optional and the percentile bands are stable over multi-year windows.

## Local files (committed to repo)

| File | Purpose |
|---|---|
| `calcpace_port.py` | Python port of calcpace: Minetti gradient, El Helou/Ely heat, NCAA altitude, Riegel/Daniels race predictions, VDOT |
| `data/benchmarks/running_percentiles.csv` | Age/sex running percentile bands (Italian Athletics, CC0) |
| `data/benchmarks/lift_percentiles.csv` | Age/sex/weight-class lift percentile bands (LiftGauge, CC0) |
| `data/exercise_coefficients.json` | Extracted Hevy `GROUP_COEFFS` (MIT) |
| `data/strength-report-2026.csv` | Hardy progression benchmarks (CC BY 4.0) |
| `scripts/build_*.py` | Rebuild compact benchmark files; raw downloads stay in `data/raw/` |
| `scripts/setup_charts.py` | Create the Dashboard charts once |
| `scripts/rebuild_chart_data.py` | Backfill the Charts tab from the progression table |
| `deploy/trainer.{service,timer}` | VPS systemd units |
| `tests/test_main.py` | Stdlib `unittest` checks for parsers, benchmarks, charts, aggregation |

## Decision-making process

DeepSeek follows a **linear, hierarchical reasoning** process with **bidirectional inter-layer dialogue**:

1. **Macro (Strategy)** — Read Strategy. Determine if current macro-cycle is optimal. Include desired body-fat percentage goal, dietary pattern, and constraints. Search meta-analyses if change needed.
2. **Meso (Weekly Schedule)** — Decide distribution of gym/running/cardio/rest days. Check Health for active conditions. Consider recent other activities' calories and muscle groups for recovery/load. Search literature on weekly frequency/periodisation if uncertain.
3. **Micro (Daily Plan)** — Select exercises/routes, sets/reps, paces, meals. Query Exercise, Pantry, wger. Calculate maintenance/target calories, macros/micros, and water. Search for specific protocols if needed.
4. **Inter-layer feedback** — Micro findings (e.g., new exercise causes soreness, diet affects recovery) can trigger macro adjustments. Macro decisions (e.g., strength block, fat-loss phase) may require micro interventions (supplements, meal timing, activity modifications).

Prompt enforces this order and requires rationales and citations for every decision. Arbitrary decisions (e.g., exercise order) must be flagged as such, with alternatives offered.

When a day is not marked complete, treat its data as partial: use what is present, and avoid assuming missing data means zero or default.

## Citation & skepticism

- **No scoring system.** DeepSeek uses qualitative judgment.
- **For sports science:** prefer meta-analyses, systematic reviews, RCTs. Be skeptical of predatory journals, low-quality authors.
- **For practical advice (recipes, technique):** favour highly upvoted Reddit posts with sources and supportive comments; respected coaches with track records who cite science.
- **Always be skeptical** — check recency, author reputation, journal integrity, consensus across independent sources.
- **If source is weak:** find a better one or propose an experiment to gather personal data.
- **Arbitrary decisions** must be stated as such, with rationale and alternatives.
- **Question everything:** DeepSeek is instructed to question the architecture itself — subject to user approval (see *Design changes require confirmation*).

## Web search

- **Daily:** enabled for practical queries and specific research checks during the daily run.
- **4-week review:** uses web search and PubMed to check strategies against current evidence.
- **Transport & budget:** research runs through the DeepSeek Search & Fetch API (`/v1/tools/search`, `/v1/tools/fetch`), which is metered/billed per successful request `main.py` caps calls per run via `RESEARCH_SEARCH_BUDGET` / `RESEARCH_FETCH_BUDGET`. PubMed checks use `site:pubmed.ncbi.nlm.nih.gov` through the same `web_search` tool; there is no separate E-utilities client.

## Weather

- **Source:** Open-Meteo (no key).
- **Granularity:** hourly forecast, up to 16 days.
- **Usage:** climate-adjusted running calculations (heat, humidity, altitude via `calcpace_port`); Telegram summary (e.g., "will rain 2–10pm", "very hot from 4pm").
- **Location:** stored in Health sheet; **edited manually** if user travels.

## Telegram (one-way)

Telegram delivers the day's plan and weather summary. **No user input via Telegram.** All input goes directly into Google Sheets (left column for recommendations is written by DeepSeek; right column is filled by the user; completeness marker is set by the user). No message history is stored or sent to DeepSeek.

**Message cadence:**
- Daily: 1 message (the plan + weather).
- Every 4 weeks: 1 additional message (long-term decisions from the review).

## Error handling & logging

- Standard logging to stdout.
- Telegram alert on critical failure only.
- No dry-run mode.

## Setup

### 1. Google Cloud & Sheets

Service account with Editor access on the target spreadsheet. Credentials JSON (with `client_email`, `private_key`, embedded `SHEET_ID`) lives in the repo directory for local runs (git-ignored) or is injected as `GOOGLE_SHEETS_JSON` / `GOOGLE_SHEETS_KEY` env vars.

### 2. Download and prepare offline datasets

```bash
# Lifting benchmarks
curl -o data/strength-report-2026.csv https://hardy.app/strength-report-data.csv

# Hevy coefficients -> data/exercise_coefficients.json
python scripts/build_exercise_coefficients.py

# Running percentile bands (Italian Athletics, CC0) -> data/benchmarks/running_percentiles.csv
python scripts/build_running_benchmarks.py

# Lift percentile bands (LiftGauge/OpenPowerlifting, CC0) -> data/benchmarks/lift_percentiles.csv
python scripts/build_lift_benchmarks.py
```

Raw source downloads go to `data/raw/` (gitignored); only the compact CSVs are committed.

### 3. Create sheet tabs

```bash
python setup_sheets.py                 # idempotent: create/repair tabs
python setup_sheets.py --reset-rolling # after a schema change: backs up Health/Diet/Exercise, keeps Strategy/Pantry/Patterns
python scripts/setup_charts.py         # create the 7 Dashboard charts (once)
```

### 4. GitHub Secrets

| Secret | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API key (required) |
| `GOOGLE_SHEETS_JSON` | Service-account JSON, single line |
| `GOOGLE_SHEETS_KEY` | Spreadsheet ID |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat to message |
| `USDA_API_KEY` | USDA FoodData Central key (free) |

Optional tuning: `DEEPSEEK_MODEL` (default `deepseek-chat`), `DEEPSEEK_MAX_TOKENS`, `DEEPSEEK_MAX_TOOL_ROUNDS`, `RESEARCH_SEARCH_BUDGET`, `RESEARCH_FETCH_BUDGET`.

**No keys needed for:** wger, PubMed, Fitness Volt, Open-Meteo.

### 5. Schedule

GitHub Actions workflow runs on `cron: "0 5 * * *"` (05:00 UTC). Use `workflow_dispatch` for manual testing. The planned VPS timer runs at 06:15 Europe/Berlin (see VPS deployment).

## Local run

```bash
pip install -r requirements.txt
python scripts/build_exercise_coefficients.py
python scripts/build_running_benchmarks.py
python scripts/build_lift_benchmarks.py
python setup_sheets.py --reset-rolling
python scripts/setup_charts.py
python main.py
python -m unittest discover -s tests -v
```

**Dependencies:** `gspread`, `requests`, `strength-utils`, `pandas`, `tenacity`.

## Handoff: implemented vs unresolved

This section is the starting point for a fresh context. The working tree is clean and the last pushed commit is the source of truth.

### Implemented

- Two-column daily blocks (`Recommended | Done`) with a `Complete` row; 14-day Exercise, 7-day Diet, 35-day Health raw windows.
- 12-week summaries per domain and a 104-week progression table on Exercise.
- Lift parsing with names, sets/reps/weight, lb→kg, bare `done` as prescribed, and Hevy coefficient normalisation (`reference_e1RM = e1RM / coefficient`).
- Five stable lift benchmarks: Squat, Bench, Posterior, OHP, Pull.
- Running normalisation through `calcpace_port`; weekly best 5K and 400m equivalents.
- Niche metrics (jump, broad jump, 10m sprint, mobility) and a one-line `Niche_trend` cell.
- Compact benchmark files:
  - `data/benchmarks/running_percentiles.csv` from Italian Athletics (CC0), sex × 5-year age band × 400/800/5000/10000 m × p10–p90.
  - `data/benchmarks/lift_percentiles.csv` from LiftGauge/OpenPowerlifting (CC0), sex × S/B/D × IPF weight class × age × p10–p90.
  - `data/exercise_coefficients.json` from Hevy Ranks (MIT).
- `Charts` tab (machine data) and `Dashboard` tab with 7 line charts; bands are constant p10–p90 series because Sheets has no native horizontal reference-line field.
- `Birth_year` in Health so band lookup can use exact age.
- VPS unit files in `deploy/` (`06:15 Europe/Berlin`, `Persistent=true`, `TimeoutStartSec=1800`).
- 16 stdlib tests pass.

### Target activity/progression model (definition of done)

The next implementation should preserve one free-text `Done` cell per activity row, with optional structured syntax:

```text
run: 5k @ Z2, hilly, 28:00; 6x400m @ 4:00/km, 90s rest
lift: OHP 3x6@40kg RPE8; Squat 3x5@100kg
hike: 2h, 900m gain
swim: 1km, 30min
hybrid: squat-shoulder press 3x6@...   # do not normalise unless explicitly split
```

Required behaviour:

1. **Classify each activity** (run, lift, hike, swim, cycle, mobility/plyo, other) before applying any conversion.
2. **Run segments:** split a session into aerobic and interval portions; normalise only the relevant portion. A 5K Zone 2 hilly run feeds the 5K curve; `6x400m` feeds the 400m anaerobic curve. Extract grade/weather/altitude when present; otherwise use documented defaults.
3. **Non-running activities:** hikes, swims, cycles and similar do not get running equivalents. Count calories/recovery context and keep the free text; optionally add niche metrics manually.
4. **Lifts:** parse named sets; map to a benchmark; apply Hevy coefficients (`reference_e1RM = e1RM / coefficient`). Hybrids such as squat-shoulder press are **not normalised by default**; split them only when the log explicitly provides two exercises. If a hybrid must be included, use weekly best, never weekly mean.
5. **Aggregation:** configurable per metric. Default weekly best for lifts/runs; weekly median for bodyweight/sleep; do not average incomparable or hybrid movements.
6. **Niche tracking:** free-form text is first-class. Keep a short `Niche_trend` summary (e.g. jump 100→120 cm after weighted squat jumps) and update it only when the change is meaningful. Do not force mobility/plyo work through lifting conversions.
7. **Long-term charts:** main movement lifts, Olympic lifts for mobility/plyo, and aerobic/anaerobic representative runs. Each has age/sex bands (and weight class for lifts) at p10–p90. Bands are **horizontal** because the x-axis is time and the y-axis is performance; the implementation uses constant-value series. Open decision: number of bands and styling.
8. **Daily context:** only the recent rolling window needed per domain, current Strategy, and a short progression slice — not the full history.
9. **Review context:** long-term progression (about 1–2 years), weekly summaries, health/diet event windows, current Strategy and pattern history. The review should detect dips/rises and correlate them with text events (HIIT started, veganism started, gym hours ended, poor sleep, allergies).
10. **Review pre-run:** a separate run before the daily run reads only long-term data, updates Strategy, then the daily run starts from the updated Strategy. Implement as `--review`/`review.py` and run it from the VPS timer before 06:15.

### Open decisions / research needed

These are intentionally not fixed yet:

- **Per-domain raw windows.** Sleep debt ~2 weeks, menstrual cycle ~4 weeks, training recovery ~1 week, diet likely shorter. Current defaults are Exercise 14d, Diet 7d, Health 35d; change the `WINDOW_DAYS` dict once decided.
- **Aggregation per metric.** Weekly best vs weekly average vs median. Current default: weekly best for lifts/runs, weekly median for bodyweight and sleep. Hybrid lifts must never enter a weekly mean.
- **Review cadence and ordering.** Current default is 4 weeks inside the daily call. Target is a separate review pre-run before the daily run; cadence may change after research.
- **Olympic lift scoring.** Absolute kg, bodyweight ratio, or Sinclair/GAMX-style age/bodyweight adjustment.
- **OHP/Pull bands.** Need a reachable Fitness Volt cache/API or another licensed source; otherwise those charts stay user-only.
- **Chart band count.** p10–p90 is currently all nine; p10/p25/p50/p75/p90 is the likely cleaner default.
- **Hybrid lift policy.** Default is no normalisation; split only when the log explicitly provides two exercises.
- **Non-running activity calories.** Hikes/swims/cycles are recovery/calorie context only; decide the calorie model and whether it is stored or just sent to DeepSeek.

### Unresolved / next implementation

1. **Activity parsing is still shallow.** `_parse_runs()` only extracts distance + finish time. It does not parse zone, terrain, elevation, weather, intervals, or hike/swim/cycle text. `collapse_logs()` calls `calcpace_port.normalise_seconds()` with defaults (`grade=0`, `temp=8`, `alt=0`). Next: a structured activity parser that isolates warm-up/interval/cooldown segments, extracts terrain/weather if present, and only normalises the relevant segment. A 5K Zone 2 hilly run contributes to the aerobic curve; a 400m interval contributes to the anaerobic curve. Do not apply running equivalences to hiking/swimming/cycling; those are calories/recovery context only.
2. **Hybrid lifts are not split.** `_benchmark_lift()` maps "squat shoulder press" to Squat because "squat" matches first. Do not normalise hybrids by default; keep them in free-form `Done`/`Niche_trend`, or add an explicit split syntax (e.g. `Squat 3x5@100 / OHP 3x6@40`) before mapping. If hybrids must enter progression, use weekly best rather than mean to limit skew.
3. **OHP/Pull percentile bands are missing.** LiftGauge only covers Squat/Bench/Deadlift. Fitness Volt `fvss/v1` does cover OHP/Row/Pull-up (CC BY 4.0), but the site is Cloudflare-blocked from this environment. Options: run the builder from a different network, add a cached JSON fixture, or leave those two charts user-only. Do not fabricate bands.
4. **Olympic lifts are not implemented.** Add Snatch, Clean & Jerk and Total as progression/chart metrics for mobility/plyometric tracking. Candidate sources: World Masters Weightlifting (OSF `wy5mr`) and OpenPowerlifting-adjacent data if suitable. Define whether to store absolute kg, bodyweight ratio, or a Sinclair/GAMX-style age/bodyweight-adjusted score.
5. **Timeframes need one deliberate pass.** Current constants at the top of `main.py`: `WINDOW_DAYS = {exercise: 14, diet: 7, health: 35}`, `WEEKLY_SUMMARY_WEEKS = 12`, `PROGRESSION_WEEKS = 104`. Research/decide: sleep debt ~2 weeks, menstrual cycle ~4 weeks, training recovery ~1 week, diet shorter. Change the dict, not the pipeline.
6. **The 4-week review is not a separate pre-run.** `run()` currently asks the daily DeepSeek call for the review and then applies Strategy updates in the same run. The requested design is: review run first (long-term data only, update Strategy), then normal daily run using the updated Strategy. Implement as `python main.py --review` or a separate `review.py`, with the systemd timer running review then daily, or a second timer before 06:15.
7. **Illness/allergy snapshots are not stored.** Patterns capture events, but there is no “around this illness” window linking sleep, diet, allergies and performance. Add a snapshot block or a `Health_event` reference; use it in the review.
8. **Niche history is only one cell.** `Niche_trend` is overwritten daily. Add an append-only niche summary or weekly niche rows; only write when the daily run has a meaningful change.
9. **Chart data only writes current/previous weeks.** `write_chart_data()` is intentionally limited to avoid quota; run `scripts/rebuild_chart_data.py` after schema changes or to backfill. `Charts` stays header-only until a progression row and `Birth_year`/`Gender`/`Weight_kg` exist.
10. **VPS handover.** `deploy/trainer.service` and `deploy/trainer.timer` are ready; the VPS is already provisioned in Europe/Berlin. Disable the GitHub Actions schedule when the VPS timer becomes primary, otherwise both write to the sheet.

### Current caveats that are intentional

- Google Sheets has no native horizontal chart reference lines; bands are added as constant series.
- Charts API cannot fully style charts; manual colours/dashes may be needed.
- Fitness Volt is used only where reachable; OHP/Pull bands are absent rather than guessed.
- Raw datasets are not committed; only compact aggregates are.

## Caveats

- **Google Fit is not used.** Sleep, steps, and resting HR are input manually (or via free text in Health).
- **`calcpace` is Ruby.** Formulas ported to a local Python module (`calcpace_port.py`). No Ruby runtime dependency.
- **USDA demo key** has a 30 req/hr limit. Get a free personal key (1,000 req/hr) at fdc.nal.usda.gov — takes 30 seconds.
- **Fitness Volt API** returns two separate populations (verified competition vs. self-reported gym). Never blended. Attribution required (CC BY 4.0).
- **Hardy CSV** cannot answer bodyweight-relative strength questions. Fitness Volt covers that.
- **Running bands** come from Italian Athletics (CC0, age/sex); NRCD (CC BY 4.0) is gender/grade only and is secondary.
- **Lift bands** come from LiftGauge/OpenPowerlifting (CC0); OHP/Pull bands are absent because no reachable equivalent dataset was found.
- **Other activities** (hiking, swimming, cycling, etc.) are not converted to running equivalents. They are logged separately, used for short-term calorie/recovery context, and do not enter long-term progression graphs.
- **Brain endurance training** is not implemented; may be added later to Strategy.
- **Reaction/allergy tracking** is caught via Patterns from the "done" column text, not a dedicated flag column.
- **Completeness markers** are required for Diet, Exercise, and Health. Incomplete days are partial data, not zero/default.
