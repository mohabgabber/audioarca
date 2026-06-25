from allauth.account.forms import SignupForm
from django import forms
from django.contrib.auth import get_user_model
from django.utils.text import slugify


class CustomSignupForm(SignupForm):
    """Allauth signup form that collects names and enforces password widgets."""

    first_name = forms.CharField(max_length=200, min_length=2)
    last_name = forms.CharField(max_length=200, min_length=2)
    email = forms.EmailField()
    password1 = forms.CharField(strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(strip=False, widget=forms.PasswordInput)

    @staticmethod
    def _build_unique_username(email: str) -> str:
        """Build a deterministic unique username from email local-part."""

        user_model = get_user_model()
        base = slugify(email.split("@", maxsplit=1)[0]) or "user"
        candidate = base
        suffix = 1
        while user_model.objects.filter(username=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        if not user.username:
            user.username = self._build_unique_username(self.cleaned_data["email"])
        user.save(update_fields=["first_name", "last_name", "username"])
        return user


class AccountVerify(forms.Form):
    """Lightweight verification form used before critical account actions."""

    password = forms.CharField(max_length=500, strip=False, widget=forms.PasswordInput)


# class RegisterForm(UserCreationForm):
#     first_name = forms.CharField(max_length=200, min_length=2)
#     last_name = forms.CharField(max_length=200, min_length=2)
#     email = forms.EmailField(min_length=5)
#     remember_me = forms.BooleanField(
#         required=False, widget=forms.CheckboxInput())

#     class Meta:
#         model = get_user_model()
#         fields = ["email", "first_name",
#                   "last_name", "password1", "password2"]

#     def clean_email(self):
#         mail = self.cleaned_data["email"].lower()
#         if get_user_model().objects.filter(email=mail).exists():
#             raise forms.ValidationError("This email address is already in use")
#         return mail.lower()

#     def save(self, commit=False):
#         user = super().save(commit=False)
#         user.email = self.cleaned_data["email"]
#         if commit:
#             user.save()
#         return user
