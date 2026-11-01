"""
Centralized configuration, loaded from environment variables (.env supported).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- MinIO (the only storage backend) ---
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_SECURE: bool = os.getenv("MINIO_SECURE", "false").lower() == "true"

    # Single bucket, split by key prefix (e.g. "templates/..." and "generated/...")
    BUCKET: str = os.getenv("BUCKET", "documents")
    TEMPLATES_PREFIX: str = os.getenv("TEMPLATES_PREFIX", "templates/")
    GENERATED_PREFIX: str = os.getenv("GENERATED_PREFIX", "generated/")

    # Presigned URL expiry (seconds) for download links (MinIO backend only)
    PRESIGNED_URL_EXPIRY: int = int(os.getenv("PRESIGNED_URL_EXPIRY", "3600"))

    # Object key of the template /generate falls back to when a request
    # doesn't specify template_id (e.g. "templates/abc123_proposal.docx").
    DEFAULT_TEMPLATE_ID: str = os.getenv("DEFAULT_TEMPLATE_ID", "")


settings = Settings()
