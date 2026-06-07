# Product Guidelines

## Engineering Principles
- **Fail Loudly**: Exceptions are preferred over silently incorrect results (e.g., using `validate="one_to_one"` on merges).
- **Strict Validation**: Enforce strict data typing using Pydantic at all application layers.
- **Performance First**: Optimize code execution time where appropriate without sacrificing correctness.

## API & Integration Design
- **Idempotent Operations**: All ingestion operations must check for existing records before writing, making them safe to re-run.
- **RESTful Design**: The prediction API should follow standard HTTP verbs and clear resource paths.
- **Low Latency**: Focus on minimal response times for prediction requests.

## Architectural Guidelines
- **Storage Abstraction**: Business logic must never talk directly to SQLite or BigQuery. All data access goes through the `DataStore` interface.
- **High Modularity**: Keep components decoupled and easily testable.
- **Experimental Focus**: Maintain an architecture that optimizes for fast experimental cycles in notebooks.