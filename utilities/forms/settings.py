from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm


class UpdateUserForm(forms.ModelForm):
    """Profile/contact update form shown in dashboard settings."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    class Meta:
        model = get_user_model()
        fields = [
            "email",
            "first_name",
            "last_name",
            "description",
            "profile_pic",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }


class UpdateUserSocialForm(forms.ModelForm):
    """Optional social links kept on the user profile."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    class Meta:
        model = get_user_model()
        fields = ["github", "facebook", "instagram", "linkedin"]


class UpdatePasswordForm(PasswordChangeForm):
    """Wrap Django's password change form with explicit password widgets."""

    old_password = forms.CharField(strip=False, widget=forms.PasswordInput)
    new_password1 = forms.CharField(strip=False, widget=forms.PasswordInput)
    new_password2 = forms.CharField(strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
