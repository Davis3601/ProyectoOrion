# Product Guidelines

## Architecture & Design Philosophy
- **Data-Driven & Modular:** The system relies on strict abstractions, such as the `DataStore` interface, to isolate business logic from infrastructure.
- **Idempotency:** Operations like data ingestion must check for existing records and be safe to run multiple times without duplication.
- **Strict Temporal Ordering:** Feature engineering must prevent data leakage by ensuring rolling averages and metrics are computed strictly from games occurring prior to the prediction date.

## Error Handling & Data Integrity
- **Fail Loudly:** The system prefers exceptions over silent errors to ensure data integrity. Incorrect or anomalous data should halt the process rather than propagate silently.
- **Strict Validation:** Operations such as data merges must be strictly validated (e.g., ensuring `one_to_one` relationships where applicable).

## Code Style & Quality
- **Strict & Explicit:** Code must be strongly typed and explicit in its dependencies.
- **Linting & Formatting:** The project enforces strict code quality using tools like `ruff` (with a configured line length of 100).
- **Abstractions:** Business logic must not interact directly with underlying storage technologies (SQLite, BigQuery); it must communicate strictly via defined interfaces.