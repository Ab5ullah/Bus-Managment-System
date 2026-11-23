"""
Cryptographic Security Engine for E-Tickets and QR Codes.
Provides:
- HMAC-SHA256 cryptographic signing and tamper-proof verification
- Deterministic payload structure with microsecond-precision timestamps
- QR code generation into base64 PNG data URIs and PIL Images
"""
import io
import json
import hmac
import hashlib
import base64
from typing import Dict, Any, Tuple, Optional
from django.conf import settings
import qrcode
from PIL import Image


def _get_signing_key() -> bytes:
    key = getattr(settings, 'SECRET_KEY', 'busms-default-secure-qr-signing-key')
    return key.encode('utf-8')


def sign_qr_payload(data: Dict[str, Any]) -> str:
    """
    Serializes payload data deterministically, computes an HMAC-SHA256 signature,
    and returns a compact, URL-safe token: <payload_b64>.<signature_hex>
    """
    serialized = json.dumps(data, sort_keys=True, separators=(',', ':'))
    payload_bytes = serialized.encode('utf-8')
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode('utf-8').rstrip('=')

    sig = hmac.new(_get_signing_key(), payload_bytes, hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_qr_payload(token_str: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Validates cryptographic authenticity and structural integrity of a QR token.
    Returns: (is_valid: bool, payload_dict: Optional[dict], message: str)
    """
    if not token_str or '.' not in token_str:
        return False, None, "Malformed QR code token: missing delimiter."

    parts = token_str.strip().split('.', 1)
    if len(parts) != 2:
        return False, None, "Invalid QR code token structure."

    payload_b64, signature = parts[0], parts[1]

    # Re-pad base64
    pad_len = 4 - (len(payload_b64) % 4)
    if pad_len < 4:
        payload_b64 += '=' * pad_len

    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode('utf-8'))
        data = json.loads(payload_bytes.decode('utf-8'))
    except Exception as e:
        return False, None, f"Failed to decode QR code payload: {str(e)}"

    expected_sig = hmac.new(_get_signing_key(), payload_bytes, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return False, None, "Cryptographic verification failed: QR code has been tampered with."

    return True, data, "Valid cryptographic signature."


def generate_qr_code_image_base64(qr_token: str) -> str:
    """
    Generates a high-contrast QR code image encoded as a Base64 PNG data URI.
    Format: data:image/png;base64,iVBORw0KGgo...
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(qr_token)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{img_b64}"


def generate_qr_code_pil_image(qr_token: str) -> Image.Image:
    """
    Generates a PIL Image instance suitable for direct ReportLab PDF embedding.
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(qr_token)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")
