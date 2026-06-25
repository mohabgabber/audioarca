from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from dash.models import Invitation

TEST_ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
TEST_CSRF_TRUSTED_ORIGINS = ["http://testserver", "http://localhost", "http://127.0.0.1"]


@override_settings(ALLOWED_HOSTS=TEST_ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS=TEST_CSRF_TRUSTED_ORIGINS)
class DashboardAuthTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.admin_password = "AdminPass!123"
        self.viewer_password = "ViewerPass!123"
        self.admin = self.user_model.objects.create_user(
            email="admin@example.com",
            username="admin",
            first_name="Admin",
            last_name="User",
            password=self.admin_password,
            role=self.user_model.Role.ADMIN,
        )
        self.viewer = self.user_model.objects.create_user(
            email="viewer@example.com",
            username="viewer",
            first_name="View",
            last_name="User",
            password=self.viewer_password,
            role=self.user_model.Role.VIEWER,
        )

    def test_auth_pages_and_dashboard_require_login(self):
        self.assertEqual(self.client.get(reverse("account_login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("account_signup")).status_code, 200)

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response["Location"])

    def test_login_logout_and_authenticated_pages(self):
        login_response = self.client.post(
            reverse("account_login"),
            {"login": self.admin.email, "password": self.admin_password},
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response["Location"], reverse("dashboard"))

        for route_name in ("dashboard", "dashboard_settings", "user_management"):
            self.assertEqual(self.client.get(reverse(route_name)).status_code, 200)

        logout_response = self.client.post(reverse("account_logout"))
        self.assertEqual(logout_response.status_code, 302)

    def test_theme_api_persists_preference(self):
        self.client.force_login(self.admin)
        patch_response = self.client.patch(
            reverse("api_user_theme_preference"),
            data='{"theme":"dark"}',
            content_type="application/json",
        )
        get_response = self.client.get(reverse("api_user_theme_preference"))

        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(get_response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.theme_preference, "dark")
        self.assertJSONEqual(get_response.content, {"theme": "dark"})

    def test_profile_update_view_saves_user_fields(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("dashboard_settings"),
            {
                "first_name": "Updated",
                "last_name": "User",
                "email": self.admin.email,
                "description": "Forensic analyst",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, "Updated")
        self.assertEqual(self.admin.description, "Forensic analyst")

    def test_admin_invitation_flow_create_revoke_and_accept(self):
        self.client.force_login(self.admin)
        create_response = self.client.post(
            reverse("user_management"),
            {
                "action": "invite",
                "email": "invitee@example.com",
                "first_name": "Invitee",
                "last_name": "User",
                "role": self.user_model.Role.REVIEWER,
            },
        )
        self.assertEqual(create_response.status_code, 302)
        invitation = Invitation.objects.get(email="invitee@example.com")
        self.assertEqual(invitation.role, self.user_model.Role.REVIEWER)
        self.assertTrue(invitation.invite_url.endswith(f"{invitation.token}/"))

        revoke_response = self.client.post(
            reverse("user_management"),
            {"action": "revoke_invitation", "invitation_id": invitation.id},
        )
        self.assertEqual(revoke_response.status_code, 302)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, Invitation.Status.REVOKED)

        accepted = Invitation.objects.create(
            email="accept@example.com",
            first_name="Accept",
            last_name="Tester",
            role=self.user_model.Role.ANALYST,
            invited_by=self.admin,
            expires_at=timezone.now() + timedelta(days=1),
        )
        accept_response = self.client.post(
            reverse("invitation_accept", kwargs={"token": accepted.token}),
            {
                "first_name": "Accept",
                "last_name": "Tester",
                "new_password1": "InvitationPass!123",
                "new_password2": "InvitationPass!123",
            },
        )
        self.assertEqual(accept_response.status_code, 302)
        self.assertTrue(self.user_model.objects.get(email="accept@example.com").check_password("InvitationPass!123"))
        accepted.refresh_from_db()
        self.assertEqual(accepted.status, Invitation.Status.ACCEPTED)

    def test_expired_invitation_returns_404(self):
        invitation = Invitation.objects.create(
            email="expired@example.com",
            first_name="Expired",
            last_name="User",
            role=self.user_model.Role.VIEWER,
            invited_by=self.admin,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        response = self.client.get(reverse("invitation_accept", kwargs={"token": invitation.token}))
        self.assertEqual(response.status_code, 404)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, Invitation.Status.EXPIRED)

    def test_non_admin_cannot_access_user_management(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("user_management"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("dashboard"))

    def test_user_management_uses_invitations_not_direct_user_creation(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("user_management"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Create user")

    def test_direct_user_creation_action_is_rejected(self):
        self.client.force_login(self.admin)
        before_count = self.user_model.objects.count()
        response = self.client.post(
            reverse("user_management"),
            {
                "action": "create_user",
                "email": "local-create@example.com",
                "first_name": "Local",
                "last_name": "Create",
                "role": self.user_model.Role.ANALYST,
                "is_active": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Direct user creation has been removed. Use an invitation instead.")
        self.assertEqual(self.user_model.objects.count(), before_count)
