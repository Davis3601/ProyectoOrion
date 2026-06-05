# NBA Game Outcome Predictor

A machine learning pipeline that predicts the **probability of the home team winning** a regular-season NBA game, using rolling team statistics derived from historical box score data.

## Overview

The project is structured as an agentic ML workflow with four layers:

1. **Ingestion** — Downloads raw game schedules and box scores from the NBA API and stores them locally (SQLite) or in the cloud (BigQuery + GCS).
2. **Feature Engineering** — Builds pre-game features expressed as home–away differences, with strict temporal ordering to prevent data leakage. *(In progress)*
3. **Modeling** — Trains and evaluates predictive models using walk-forward validation. *(Pending)*
4. **API** — Serves predictions via a Cloud Run HTTP endpoint. *(Pending)*

**Target:** Binary classification with calibrated probability output.  
**Primary metric:** Log loss. Secondary: Brier score, accuracy, calibration diagnostics.  
**Baseline to beat:** "Always predict home win" (~57–59% accuracy). Goal: outperform an ELO model on out-of-sample log loss.

---

## Data

- **Source:** [`nba_api`](https://github.com/swar/nba_api) (unofficial NBA stats API wrapper).
- **Coverage:** 12 seasons — 2014-15 through 2025-26 — totaling **14,429 games**.
- **Training window:** 2016-17 to 2025-26 (10 seasons). The 2014-15 and 2015-16 seasons are warmup-only: they seed rolling averages for the first games of the training window and are never included as training rows.
- **Scope:** Regular season only. First 15 games per team per season are excluded from training (insufficient rolling history).

### Database schema

| Table | Description |
|---|---|
| `teams` | Static catalog of 30 NBA teams (team_id, abbreviation, name). |
| `games` | One row per game: date, teams, final score, `home_won`, `neutral_site` flag. |
| `team_game_stats` | Raw box score per team per game (FGM, FGA, 3PM, FTA, OREB, DREB, AST, STL, BLK, TOV, PF, ±). |
| `player_game_stats` | Raw box score per player per game, including DNP-bench players (minutes = NULL). |

---

## Feature Design

All strength features are expressed as **differences (home − away)** so that a positive value always favors the home team. Absolute features (rest days, neutral site flag) are kept separate.

### Planned feature groups

| Group | Features |
|---|---|
| 1 — Four Factors | `efg_diff`, `tov_rate_diff`, `oreb_rate_diff`, `ft_rate_diff` |
| 2 — Ratings | `off_rating_diff`, `def_rating_diff`, `net_rating_diff` (rolling) |
| 3 — Adjusted ratings | Opponent-quality-adjusted versions of Group 2 |
| 4 — Context | `rest_diff`, `home_b2b`, `away_b2b`, `neutral_site` |
| 5 — Player availability | Effective roster strength difference (home − away) |

All rolling averages are computed **strictly from games prior to the game being predicted** (no leakage). Validation uses walk-forward splits (train on past seasons, validate on the next).

---

## Project Status

| Phase | Status |
|---|---|
| Phase 0 — Problem definition | Complete |
| Phase 1 — Ingestion (schedules + box scores) | **Complete** |
| Phase 2 — Feature engineering | In progress |
| Phase 3 — Modeling + evaluation | Pending |
| Phase 4 — Prediction API + deployment | Pending |

---

## Tech Stack

- **Language:** Python 3.11
- **Data access:** `nba_api`, `pandas`, `pyarrow`
- **Storage (local):** SQLite + filesystem (Parquet)
- **Storage (cloud):** Google BigQuery + Cloud Storage *(pending)*
- **Config:** Pydantic Settings + `.env`
- **Deployment target:** Google Cloud Run (ingestion job + prediction service)
- **Linter:** ruff (line length 100)

---

## Installation

```bash
git clone https://github.com/arcadelife1/nba-predictor.git
cd nba-predictor

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in your settings. (Note: If `.env.example` is missing, ensure you obtain the template from the project maintainers).

```bash
cp .env.example .env
```

---

## Running the ingestion pipeline

```bash
# Populate the teams catalog (no API call needed)
python scripts/populate_teams.py

# Download game schedules (all 12 seasons)
python scripts/ingest_schedules.py

# Validate schedules
python scripts/sanity_check_schedules.py

# Download box scores (long-running; idempotent — safe to re-run)
python scripts/ingest_full_history.py

# Validate box scores
python scripts/sanity_check_boxscores.py
```

---

## Running tests

```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=nba_predictor
```

---

## Repository Structure

```
nba_predictor/
├── config.py              # Pydantic Settings + season constants
├── ingestion/
│   ├── nba_client.py      # NBA API wrapper with retry/throttle
│   ├── schedule.py        # Schedule download and normalization
│   └── boxscores.py       # Box score download and normalization
├── storage/
│   ├── base.py            # Abstract DataStore interface
│   ├── local.py           # LocalDataStore (SQLite)
│   └── cloud.py           # CloudDataStore (BigQuery + GCS) — pending
├── features/              # Pending (Phase 2)
├── models/                # Pending (Phase 3)
└── api/                   # Pending (Phase 4)

scripts/                   # Runnable entry points
notebooks/                 # Exploratory analysis
tests/                     # Unit + integration tests
```

---

## Key Design Decisions

- **Storage abstraction:** all data access goes through the `DataStore` interface. `LocalDataStore` (SQLite) and `CloudDataStore` (BigQuery) are interchangeable via `get_datastore()`, which reads `settings.mode`. Business logic never talks directly to SQLite or BigQuery.
- **Idempotent ingestion:** every ingestion operation checks for existing records before writing. Safe to re-run at any time.
- **Raw stats, not derived:** box scores store raw counts (shots attempted/made) rather than percentages. Percentages are derived during feature engineering, which avoids averaging-of-averages errors.
- **Fail loudly:** exceptions are preferred over silently incorrect results. Merges use `validate="one_to_one"` where applicable.
- **No temporal leakage:** rolling features are always computed from games strictly before the prediction date.

---

## Known Limitations

- The `nba_api` is unofficial and rate-limited; ingestion includes throttling and retries.
- Player absence reason (injury vs. rest vs. not rostered) is not available from box score endpoints. Only whether a player appeared in the box score can be determined.
- The 2025-26 season is in progress; the ingested game count will be below a full season total. This is expected.
