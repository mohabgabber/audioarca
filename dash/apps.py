from django.apps import AppConfig


class DashConfig(AppConfig):
    """Dashboard app configuration."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dash'

    def ready(self):
        # Import dashboard signals on startup
        import dash.signals  # noqa: F401
