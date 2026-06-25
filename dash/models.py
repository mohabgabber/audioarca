import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django_resized import ResizedImageField


class UserModel(AbstractUser):
    """Custom user model for the merged dashboard app."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        ANALYST = "analyst", "Analyst"
        REVIEWER = "reviewer", "Reviewer"
        VIEWER = "viewer", "Viewer"

    id = models.UUIDField(editable=False, primary_key=True, default=uuid.uuid4)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=200)
    last_name = models.CharField(max_length=200)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ANALYST)
    description = models.CharField(max_length=2000, blank=True, null=True)
    phone_number = models.CharField(max_length=60, blank=True, null=True)
    country = models.CharField(max_length=200, blank=True, null=True)
    city = models.CharField(max_length=200, blank=True, null=True)
    address1 = models.CharField(max_length=200, blank=True, null=True)
    address2 = models.CharField(max_length=200, blank=True, null=True)
    zip = models.CharField(max_length=20, default="1111")
    theme_preference = models.CharField(
        max_length=5,
        choices=(("light", "Light"), ("dark", "Dark")),
        default="light",
    )
    newsletter = models.BooleanField(default=True)
    instructor = models.BooleanField(default=False)
    profile_pic = ResizedImageField(
        size=[300, 300],
        crop=["middle", "center"],
        upload_to="profile/%Y/%m/%d/",
        default="default-profile.jpg",
    )
    facebook = models.URLField(default="https://facebook.com/", max_length=350)
    instagram = models.URLField(max_length=350, default="https://instagram.com/")
    linkedin = models.URLField(max_length=350, default="https://linkedin.com/in/")
    github = models.URLField(max_length=350, default="https://github.com/")
    registered = models.BooleanField(default=False)
    occupation = models.CharField(max_length=500, default="Awesome Hacker")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username", "first_name", "last_name"]

    @property
    def full_name(self) -> str:
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name or self.email

    def can_manage_users(self) -> bool:
        return self.is_superuser or self.role == self.Role.ADMIN

    def can_review_cases(self) -> bool:
        return self.is_authenticated

    def can_create_cases(self) -> bool:
        return self.is_authenticated


class Invitation(models.Model):
    """User invitation with tokenized onboarding."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    first_name = models.CharField(max_length=200)
    last_name = models.CharField(max_length=200)
    role = models.CharField(
        max_length=20,
        choices=UserModel.Role.choices,
        default=UserModel.Role.VIEWER,
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    invited_by = models.ForeignKey(
        "dash.UserModel",
        on_delete=models.PROTECT,
        related_name="sent_invitations",
    )
    accepted_by = models.ForeignKey(
        "dash.UserModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_invitations",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    invite_url = models.URLField(blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.email} ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.PENDING and not self.is_expired

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def mark_expired_if_needed(self) -> None:
        if self.status == self.Status.PENDING and self.is_expired:
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status", "updated_at"])


class AnalysisCase(models.Model):
    """Case record for forensic phonetic or forensic linguistic analysis."""

    class AnalysisType(models.TextChoices):
        PHONETIC = "phonetic", "Phonetic"
        LINGUISTIC = "linguistic", "Linguistic"

    name = models.CharField(max_length=255)
    description = models.TextField()
    analysis_type = models.CharField(max_length=10, choices=AnalysisType.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-created_at")

    def __str__(self):
        return self.name
