"""
Development middleware for printing API request/response payloads to the server console.
Active only when DEBUG=True and not running under pytest.
"""
import json
import sys
import time
from django.conf import settings


class APIDebugLoggingMiddleware:
    """Prints HTTP method, path, status code, and JSON response body for /api/ calls in DEBUG mode."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.DEBUG or not request.path.startswith('/api/') or 'pytest' in sys.argv[0]:
            return self.get_response(request)

        start = time.monotonic()
        response = self.get_response(request)
        duration_ms = int((time.monotonic() - start) * 1000)

        full_path = request.get_full_path()
        print(f"\n[API] {request.method} {full_path} -> {response.status_code} ({duration_ms}ms)", flush=True)

        content_type = response.get('Content-Type', '')
        if not getattr(response, 'streaming', False) and 'application/json' in content_type:
            try:
                raw = response.content.decode('utf-8', errors='replace')
                try:
                    parsed = json.loads(raw)
                    formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
                except Exception:
                    formatted = raw
                if len(formatted) > 3000:
                    formatted = formatted[:3000] + "\n... [truncated]"
                print(f"[API Response]\n{formatted}\n", flush=True)
            except Exception:
                pass

        return response
