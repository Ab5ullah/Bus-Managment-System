"""
Custom authentication classes for the Bus Ticket Management System.
"""
from rest_framework.authentication import SessionAuthentication


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """
    SessionAuthentication subclass that exempts CSRF token validation.

    In a REST API architecture using JWT tokens alongside a web admin or
    browser-based interactive UI, browser session cookies can trigger DRF's
    built-in SessionAuthentication CSRF check on API endpoints, causing
    spurious 'CSRF Failed: CSRF token missing' rejections.

    This class allows authenticated browser sessions (e.g. Django Admin sessions)
    to interact with API endpoints without failing CSRF token enforcement.
    """

    def enforce_csrf(self, request):
        return  # Bypass CSRF enforcement for REST API endpoints
