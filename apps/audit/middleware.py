"""
Middleware for automatic AuditLog recording on state-changing API endpoints.
"""
import json
import logging
from .models import AuditAction, AuditLog

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    'password', 'password1', 'password2', 'old_password', 'new_password',
    'token', 'access', 'refresh', 'secret', 'otp', 'otp_code', 'card_number', 'cvv',
}


def _sanitize_payload(data):
    """Recursively mask sensitive fields in request payloads."""
    if isinstance(data, dict):
        return {
            k: ('***MASKED***' if str(k).lower() in SENSITIVE_KEYS else _sanitize_payload(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_sanitize_payload(item) for item in data[:50]]
    return data


def _get_client_ip(request) -> str | None:
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


class AuditLogMiddleware:
    """
    Records state-changing /api/ requests (POST, PUT, PATCH, DELETE)
    that succeed (2xx/3xx) into AuditLog.
    """
    AUDITED_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Capture raw body before view consumes stream
        parsed_body = {}
        if request.method in self.AUDITED_METHODS and request.path.startswith('/api/'):
            try:
                if request.body:
                    raw = json.loads(request.body.decode('utf-8'))
                    if isinstance(raw, dict):
                        parsed_body = _sanitize_payload(raw)
            except Exception:
                parsed_body = {}

        response = self.get_response(request)

        if (
            request.method in self.AUDITED_METHODS
            and request.path.startswith('/api/')
            and 200 <= response.status_code < 400
        ):
            try:
                path_lower = request.path.lower()
                if 'cancel' in path_lower:
                    action = AuditAction.CANCEL
                elif request.method == 'POST':
                    action = AuditAction.CREATE
                elif request.method in ('PUT', 'PATCH'):
                    action = AuditAction.UPDATE
                else:
                    action = AuditAction.DELETE

                user = getattr(request, 'user', None)
                if user is not None and not getattr(user, 'is_authenticated', False):
                    user = None

                AuditLog.objects.create(
                    user=user,
                    action=action,
                    resource=request.path[:255],
                    http_method=request.method,
                    status_code=response.status_code,
                    ip_address=_get_client_ip(request),
                    user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:255],
                    payload_diff=parsed_body,
                )
            except Exception as exc:
                logger.warning("AuditLog recording skipped: %s", exc)

        return response
