from __future__ import annotations

from src.shared.exceptions import BadRequestError

EMAIL_MAX_LENGTH = 320


def normalize_email(value: str) -> str:
    """Return the canonical form of an address.

    Every caller must share this definition: the filter is keyed on the result,
    so a second normalization would fork the key space.
    """
    email = value.strip().lower()
    if "@" not in email or len(email) > EMAIL_MAX_LENGTH:
        raise BadRequestError("A valid email address is required.")
    return email
