from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateArtifactStorage(FileSystemStorage):
    """Filesystem-backed storage for private forensic artifacts."""

    def __init__(self, *args, **kwargs):
        location = kwargs.pop("location", Path(settings.PRIVATE_MEDIA_ROOT))
        base_url = kwargs.pop("base_url", None)
        super().__init__(location=location, base_url=base_url, *args, **kwargs)


private_artifact_storage = PrivateArtifactStorage()
