"""MinIO storage — re-exports a uniform interface:

  ensure_buckets(), new_object_id(), upload_bytes(key, data, content_type),
  download_bytes(key), object_exists(key), list_objects(prefix),
  get_download_url(key)
"""
from app.storage.exceptions import ObjectNotFoundError
from app.storage import minio_backend as _backend

ensure_buckets = _backend.ensure_buckets
new_object_id = _backend.new_object_id
upload_bytes = _backend.upload_bytes
download_bytes = _backend.download_bytes
object_exists = _backend.object_exists
list_objects = _backend.list_objects
get_download_url = _backend.get_download_url
