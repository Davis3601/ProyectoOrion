# Tech Stack

## Core Language & Environment
- **Language:** Python 3.11

## Data Processing & Ingestion
- **Data Manipulation:** `pandas`, `pyarrow`
- **External APIs:** `nba-api` (for schedule and box score ingestion)

## Storage & Database
- **Local Storage:** SQLite (relational data), Filesystem / Parquet (columnar data)
- **Cloud Infrastructure:** Google BigQuery (Data Warehouse), Google Cloud Storage (GCS)

## Configuration & Tooling
- **Configuration Management:** `pydantic`, `pydantic-settings`, `python-dotenv`
- **Linting & Formatting:** `ruff` (configured for line length 100)
- **Testing:** `pytest`, `pytest-cov`

## Deployment
- **Target Platform:** Google Cloud Run (serving the HTTP endpoint and running ingestion jobs)