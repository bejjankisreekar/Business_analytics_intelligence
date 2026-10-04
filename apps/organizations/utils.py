import secrets


def generate_organization_code() -> str:
    """8-char uppercase hex code, e.g. 'A1F93B2C'."""
    return secrets.token_hex(4).upper()
