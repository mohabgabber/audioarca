from django import forms


class AddBookmark(forms.Form):
    """Minimal form to bookmark a course by UUID."""

    course_id = forms.UUIDField(required=True)
