from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.http import HttpRequest


class AuthBackend(ModelBackend):
    """Authenticate users by email (case-insensitive)."""

    def authenticate(
        self,
        request: HttpRequest,
        username: str | None = None,
        password: str | None = None,
        **kwargs,
    ):
        login_identifier = username or kwargs.get("email") or kwargs.get("login")
        if not login_identifier or not password:
            return None

        user_model = get_user_model()
        try:
            user = user_model.objects.get(email__iexact=login_identifier)
        except user_model.DoesNotExist:
            return None

        if user.is_active and user.check_password(password):
            return user
        return None


def user_display(user):
    """Consistent display name used by django-allauth."""

    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name or user.email
