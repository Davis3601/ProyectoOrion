"""DataStore Factory: decides the implementation based on configuration."""
from ..config import settings
from .base import DataStore
from .local import LocalDataStore


def get_datastore() -> DataStore:
    """Returns the appropriate DataStore based on settings.mode."""
    if settings.mode == "local":
        return LocalDataStore(
            db_path=settings.db_path,
            raw_dir=settings.raw_dir,
        )
    elif settings.mode == "cloud":
        from .cloud import CloudDataStore  # Deferred import
        return CloudDataStore(
            project_id=settings.gcp_project_id,
            dataset=settings.bq_dataset,
            bucket_name=settings.gcs_bucket,
        )
    else:
        raise ValueError(f"Unknown mode: {settings.mode}")
