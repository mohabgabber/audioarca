from django.apps import AppConfig


class ForensicsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forensics"
    verbose_name = "Forensic Analysis"

    def ready(self):
        import forensics.signals  # noqa: F401
