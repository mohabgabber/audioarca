from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AnalysisCase, Invitation, UserModel


@admin.register(UserModel)
class UserModelAdmin(UserAdmin):
    """Admin registration for custom user model."""

    model = UserModel
    ordering = ("email",)
    list_display = (
        "email",
        "first_name",
        "last_name",
        "role",
        "theme_preference",
        "is_staff",
        "is_active",
    )


@admin.register(AnalysisCase)
class AnalysisCaseAdmin(admin.ModelAdmin):
    """Admin registration for analysis cases."""

    list_display = ("name", "analysis_type", "created_at", "updated_at")
    list_filter = ("analysis_type", "created_at", "updated_at")
    search_fields = ("name", "description")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "role", "status", "invited_by", "expires_at", "accepted_at")
    list_filter = ("role", "status")
    search_fields = ("email", "first_name", "last_name")
