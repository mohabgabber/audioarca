from django.utils.crypto import get_random_string


def messagereturn(type: int = 0, msg: str = "successful") -> str:
    """Return a bootstrap alert snippet for quick template rendering."""
    type_tag = "success"
    if type == 1:
        type_tag = "warning"

    message = f"""<div class=\"alert alert-{type_tag}
        \"><strong>{msg}</strong></div>"""

    return message


def generate_voucher(length=8):
    """Create a random voucher code with mixed casing."""
    return get_random_string(
        length=length,
        allowed_chars="ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefghijklmnopqrstuvwxyz",
    )


def default_course_content() -> dict[str, list[dict]]:
    """Seed structure for course content JSONField defaults."""
    return {"content": [{}]}


def default_outcomes() -> dict[str, list]:
    """Seed structure for learning outcomes JSONField defaults."""
    return {"outcomes": []}
