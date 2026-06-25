from __future__ import annotations

from datetime import timedelta

from allauth.account.views import LoginView, PasswordChangeView, SignupView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from dash.models import Invitation
from forensics.services.cases import invite_url_for_token
from forensics.services.monitoring import (
    adverse_condition_summary,
    dashboard_metrics,
    integrity_summary,
    operational_summary,
    recent_cases,
)
from utilities.forms.admin import InvitationAcceptForm, InvitationForm
from utilities.forms.settings import UpdatePasswordForm, UpdateUserForm
from utilities.forms.user import CustomSignupForm


class DashboardTemplateView(LoginRequiredMixin, TemplateView):
    current_section = "dashboard"
    page_heading = "Dashboard"
    page_subtitle = "Operational overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_section"] = self.current_section
        context["page_heading"] = self.page_heading
        context["page_subtitle"] = self.page_subtitle
        context["current_date"] = timezone.now().strftime("%B %d, %Y")
        return context


class AdminRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.can_manage_users()

    def handle_no_permission(self):
        messages.error(self.request, "Administrator access is required for that page.")
        return redirect("dashboard")


class DashboardHome(DashboardTemplateView):
    template_name = "dash/home.html"
    current_section = "dashboard"
    page_heading = "Forensic Operations Dashboard"
    page_subtitle = "Live case, integrity, and queue status for the internal toolkit."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        first_name = (self.request.user.first_name or "").strip()
        context["greeting_name"] = first_name or "Analyst"
        context["metrics"] = dashboard_metrics()
        context["latest_cases"] = recent_cases()
        context["operational_summary"] = operational_summary()
        context["integrity_summary"] = integrity_summary()
        context["adverse_cases"] = adverse_condition_summary()
        return context


class DashboardSettings(DashboardTemplateView, FormView):
    template_name = "dash/settings.html"
    current_section = "settings"
    page_heading = "Settings"
    page_subtitle = "Manage your profile, password, and persisted theme preference."
    form_class = UpdateUserForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["password_form"] = kwargs.get("password_form") or UpdatePasswordForm(self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "password":
            password_form = UpdatePasswordForm(request.user, request.POST)
            profile_form = self.get_form()
            if password_form.is_valid():
                password_form.save()
                messages.success(request, "Password updated successfully.")
                return redirect("dashboard_settings")
            return self.render_to_response(self.get_context_data(form=profile_form, password_form=password_form))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Profile updated successfully.")
        return redirect("dashboard_settings")


class UserManagementView(AdminRequiredMixin, DashboardTemplateView):
    template_name = "dash/user-management.html"
    current_section = "users"
    page_heading = "User Management"
    page_subtitle = "Issue invitation links, assign roles, and manage activation."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user_model = get_user_model()
        context["invitation_form"] = kwargs.get("invitation_form") or InvitationForm()
        context["users"] = user_model.objects.order_by("email")
        invitations = Invitation.objects.select_related("invited_by", "accepted_by").order_by("-created_at")
        for invitation in invitations:
            invitation.mark_expired_if_needed()
        context["invitations"] = invitations
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        user_model = get_user_model()

        if action == "create_user":
            messages.error(request, "Direct user creation has been removed. Use an invitation instead.")
            return redirect("user_management")

        if action == "invite":
            invitation_form = InvitationForm(request.POST)
            if invitation_form.is_valid():
                invitation = invitation_form.save(commit=False)
                invitation.invited_by = request.user
                invitation.expires_at = timezone.now() + timedelta(days=settings.FORENSICS_INVITATION_EXPIRY_DAYS)
                invitation.save()
                invitation.invite_url = invite_url_for_token(request, str(invitation.token))
                invitation.save(update_fields=["invite_url", "updated_at"])
                messages.success(request, f"Invitation created for {invitation.email}.")
                return redirect("user_management")
            return self.render_to_response(self.get_context_data(invitation_form=invitation_form))

        if action == "toggle_user":
            user = get_object_or_404(user_model, pk=request.POST.get("user_id"))
            if user == request.user:
                messages.error(request, "You cannot deactivate your own account from this page.")
                return redirect("user_management")
            user.is_active = not user.is_active
            user.role = request.POST.get("role", user.role)
            user.save(update_fields=["is_active", "role", "updated_at"] if hasattr(user, "updated_at") else ["is_active", "role"])
            messages.success(request, f"Updated {user.email}.")
            return redirect("user_management")

        if action == "revoke_invitation":
            invitation = get_object_or_404(Invitation, pk=request.POST.get("invitation_id"))
            invitation.status = Invitation.Status.REVOKED
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=["status", "revoked_at", "updated_at"])
            messages.success(request, f"Revoked invitation for {invitation.email}.")
            return redirect("user_management")

        messages.error(request, "Unknown action.")
        return redirect("user_management")


class InvitationAcceptView(FormView):
    template_name = "dash/account/invitation-accept.html"
    form_class = InvitationAcceptForm

    def dispatch(self, request, *args, **kwargs):
        self.invitation = get_object_or_404(Invitation, token=kwargs["token"])
        self.invitation.mark_expired_if_needed()
        if self.invitation.status != Invitation.Status.PENDING:
            raise Http404("Invitation is no longer available.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        user_model = get_user_model()
        kwargs["user"] = user_model(
            email=self.invitation.email,
            username=CustomSignupForm._build_unique_username(self.invitation.email),
            role=self.invitation.role,
            is_active=True,
        )
        return kwargs

    def get_initial(self):
        return {
            "first_name": self.invitation.first_name,
            "last_name": self.invitation.last_name,
        }

    def form_valid(self, form):
        user = form.save(commit=False)
        user.email = self.invitation.email
        user.role = self.invitation.role
        user.is_active = True
        user.username = user.username or CustomSignupForm._build_unique_username(self.invitation.email)
        user.save()
        self.invitation.accepted_by = user
        self.invitation.accepted_at = timezone.now()
        self.invitation.status = Invitation.Status.ACCEPTED
        self.invitation.save(update_fields=["accepted_by", "accepted_at", "status", "updated_at"])
        login(self.request, user, backend="dash.auth.AuthBackend")
        messages.success(self.request, "Account activated successfully.")
        return redirect("dashboard")


class AllAuthSignin(LoginView):
    template_name = "dash/account/signin.html"


class AllAuthSignUp(SignupView):
    template_name = "dash/account/signup.html"
    form_class = CustomSignupForm


class AllAuthPasswordChange(PasswordChangeView):
    template_name = "dash/account/password-change.html"
    form_class = UpdatePasswordForm

    def get_success_url(self):
        return reverse_lazy("dashboard_settings")
