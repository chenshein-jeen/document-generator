"""MinIO storage backend (STORAGE_BACKEND=minio).

Single bucket (settings.BUCKET), split by key prefix — see app.config for
the templates/generated prefixes.
"""
import io
import uuid
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from app.config import settings
from app.storage.exceptions import ObjectNotFoundError

_client: Minio | None = None


def _get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
    return _client


def ensure_buckets() -> None:
    client = _get_client()
    if not client.bucket_exists(settings.BUCKET):
        client.make_bucket(settings.BUCKET)


def new_object_id() -> str:
    return uuid.uuid4().hex


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    client = _get_client()
    client.put_object(
        settings.BUCKET,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def download_bytes(key: str) -> bytes:
    client = _get_client()
    try:
        response = client.get_object(settings.BUCKET, key)
    except S3Error as e:
        raise ObjectNotFoundError(key) from e
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def object_exists(key: str) -> bool:
    client = _get_client()
    try:
        client.stat_object(settings.BUCKET, key)
        return True
    except S3Error:
        return False


def list_objects(prefix: str = "") -> list[str]:
    client = _get_client()
    return [
        obj.object_name
        for obj in client.list_objects(settings.BUCKET, prefix=prefix, recursive=True)
    ]


def get_download_url(key: str) -> str:
    client = _get_client()
    return client.presigned_get_object(
        settings.BUCKET,
        key,
        expires=timedelta(seconds=settings.PRESIGNED_URL_EXPIRY),
    )
