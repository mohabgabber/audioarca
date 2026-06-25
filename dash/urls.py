from allauth.account.views import LogoutView
from django.urls import path
from django.views.generic import RedirectView

from .api_views import UserThemePreferenceAPIView
from .views import (
    AllAuthPasswordChange,
    AllAuthSignUp,
    AllAuthSignin,
    DashboardHome,
    DashboardSettings,
    InvitationAcceptView,
    UserManagementView,
)

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("dashboard", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("dashboard/", DashboardHome.as_view(), name="dashboard"),
    path("settings/", DashboardSettings.as_view(), name="dashboard_settings"),
    path("users/", UserManagementView.as_view(), name="user_management"),
    path("invitations/accept/<uuid:token>/", InvitationAcceptView.as_view(), name="invitation_accept"),
    path("signin/", AllAuthSignin.as_view(), name="account_login"),
    path("signup/", AllAuthSignUp.as_view(), name="account_signup"),
    path(
        "password/change/",
        AllAuthPasswordChange.as_view(),
        name="account_change_password",
    ),
    path(
        "logout/",
        LogoutView.as_view(template_name="dash/account/logout.html"),
        name="account_logout",
    ),
    path(
        "api/user/settings/theme/",
        UserThemePreferenceAPIView.as_view(),
        name="api_user_theme_preference",
    ),
]
