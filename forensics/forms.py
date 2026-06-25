from __future__ import annotations

from django import forms
from django.conf import settings

from forensics.models import Case
from forensics.services.helpers import sniff_mime_type


class BaseCaseForm(forms.Form):
    case_name = forms.CharField(
        max_length=255,
        label="Case name",
        help_text="Use a clear investigator-facing label for the comparison request.",
    )
    case_number = forms.CharField(
        max_length=64,
        required=False,
        label="Case number",
        help_text="Leave blank to auto-generate a unique case number.",
    )
    description = forms.CharField(
        required=False,
        label="Case summary",
        help_text="Describe the submitted material, requested question, and any known cautions.",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css_class = "form-control"
            if isinstance(field.widget, forms.Textarea):
                css_class = "form-control"
            elif isinstance(field.widget, forms.FileInput):
                css_class = "form-control"
            field.widget.attrs.setdefault("class", css_class)
            field.widget.attrs.setdefault("placeholder", field.label)

    def clean_case_number(self):
        value = self.cleaned_data["case_number"].strip().upper()
        if value and Case.objects.filter(case_number=value).exists():
            raise forms.ValidationError("Case number already exists.")
        return value


class PhoneticCaseCreateForm(BaseCaseForm):
    sample_a = forms.FileField(
        label="Sample A audio",
        help_text="Accepted formats: WAV or MP3 only.",
        widget=forms.ClearableFileInput(attrs={"accept": ".wav,.mp3"}),
    )
    sample_b = forms.FileField(
        label="Sample B audio",
        help_text="Accepted formats: WAV or MP3 only.",
        widget=forms.ClearableFileInput(attrs={"accept": ".wav,.mp3"}),
    )

    def _validate_audio(self, uploaded_file, label: str):
        suffix = uploaded_file.name.rsplit(".", maxsplit=1)[-1].lower() if "." in uploaded_file.name else ""
        if f".{suffix}" not in settings.FORENSICS_ALLOWED_AUDIO_EXTENSIONS:
            raise forms.ValidationError(f"{label} must be a WAV or MP3 file.")
        mime_type = sniff_mime_type(uploaded_file)
        if mime_type not in settings.FORENSICS_ALLOWED_AUDIO_MIME_TYPES:
            raise forms.ValidationError(f"{label} has an unsupported MIME type: {mime_type}.")
        return uploaded_file

    def clean_sample_a(self):
        return self._validate_audio(self.cleaned_data["sample_a"], "Sample A")

    def clean_sample_b(self):
        return self._validate_audio(self.cleaned_data["sample_b"], "Sample B")


class LinguisticCaseCreateForm(BaseCaseForm):
    suspected_sample_text = forms.CharField(
        label="Suspected sample text",
        help_text="Paste the questioned text sample used for comparison.",
        widget=forms.Textarea(attrs={"rows": 8}),
    )
    provided_sample_text = forms.CharField(
        label="Known sample text",
        help_text="Paste the comparison text from the known or reference author.",
        widget=forms.Textarea(attrs={"rows": 8}),
    )

    def clean(self):
        cleaned = super().clean()
        for field_name in ("suspected_sample_text", "provided_sample_text"):
            if len(cleaned.get(field_name, "").strip()) < 50:
                self.add_error(field_name, "Provide at least 50 characters for meaningful stylometric analysis.")
        return cleaned


class ReviewDecisionForm(forms.Form):
    reviewer_notes = forms.CharField(
        required=False,
        label="Reviewer notes",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Record approval rationale, caveats, or follow-up actions."}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reviewer_notes"].widget.attrs.setdefault("class", "form-control")
