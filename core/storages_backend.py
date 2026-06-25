from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

try:
    from storages.backends.s3boto3 import S3Boto3Storage
except Exception as exc:  # pragma: no cover - depends on optional boto3 install
    S3Boto3Storage = None
    S3_IMPORT_ERROR = exc
else:
    S3_IMPORT_ERROR = None


if S3Boto3Storage is None:

    class _UnavailableS3Storage:
        def __init__(self, *args, **kwargs):
            raise ImproperlyConfigured(
                "USE_S3_STORAGE requires boto3 and django-storages S3 support."
            ) from S3_IMPORT_ERROR


    class StaticStorage(_UnavailableS3Storage):
        pass


    class MediaStorage(_UnavailableS3Storage):
        pass

else:

    class StaticStorage(S3Boto3Storage):
        """S3 backend for collected static files."""

        location = "static"
        custom_domain = settings.CLOUDFRONT_DOMAIN or None


    class MediaStorage(S3Boto3Storage):
        """S3 backend for user-uploaded media."""

        location = "media"
        custom_domain = settings.CLOUDFRONT_DOMAIN or None
