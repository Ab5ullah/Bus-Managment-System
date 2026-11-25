"""
Reusable domain validators for Pakistani CNIC / Government ID numbers.
"""
import re
from common.exceptions import BusinessLogicError

# Pakistani CNIC: 12345-1234567-1 or 13 raw digits
CNIC_DASHED_RE = re.compile(r'^\d{5}-\d{7}-\d$')
CNIC_DIGITS_RE = re.compile(r'^\d{13}$')
# International Passport / Govt ID fallback (alphanumeric 6-20 chars, at least 1 digit)
PASSPORT_RE = re.compile(r'^(?=.*\d)[A-Z0-9]{6,20}$')


def normalize_and_validate_cnic_or_id(value: str, required: bool = False) -> str:
    """
    Validates and normalizes a passenger CNIC or Government ID.
    - Accepts 13 digits (auto-formats to XXXXX-XXXXXXX-X)
    - Accepts dashed XXXXX-XXXXXXX-X format
    - Accepts alphanumeric passport numbers (6-20 chars containing digits)
    """
    cleaned = (value or '').strip().upper().replace(' ', '')
    if not cleaned:
        if required:
            raise BusinessLogicError("Passenger CNIC or Government ID is required for ticket issuance.")
        return ''

    if CNIC_DIGITS_RE.match(cleaned):
        return f"{cleaned[:5]}-{cleaned[5:12]}-{cleaned[12]}"

    if CNIC_DASHED_RE.match(cleaned):
        return cleaned

    if PASSPORT_RE.match(cleaned):
        return cleaned

    raise BusinessLogicError(
        f"Invalid CNIC / Government ID '{value}'. Use 13-digit CNIC (e.g. 35202-1234567-1) or valid Passport ID."
    )
