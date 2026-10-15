"""Small helpers for reading and validating HTML form fields."""


class FormError(ValueError):
    """Raised when a submitted field is missing or not a valid number."""


def text(form, name, required=True):
    value = form.get(name, "").strip()
    if required and not value:
        raise FormError(f"{name.replace('_', ' ').capitalize()} is required.")
    return value or None


def number(form, name, cast=float, required=True, positive=True):
    raw = form.get(name, "").strip()
    label = name.replace("_", " ")
    if not raw:
        if required:
            raise FormError(f"{label.capitalize()} is required.")
        return None
    try:
        value = cast(raw)
    except ValueError:
        raise FormError(f"{label.capitalize()} must be a number.") from None
    if positive and value <= 0:
        raise FormError(f"{label.capitalize()} must be greater than zero.")
    return value
