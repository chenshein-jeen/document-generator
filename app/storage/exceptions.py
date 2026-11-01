class ObjectNotFoundError(Exception):
    """Raised by download_bytes() when the given key doesn't exist in storage."""
