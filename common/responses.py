"""
Standardized API response helper utilities.
"""
from rest_framework import status
from rest_framework.response import Response


def api_success(data=None, message="Operation successful.", status_code=status.HTTP_200_OK, extra=None):
    """
    Produce a consistent success response payload.
    """
    payload = {
        "success": True,
        "message": message,
        "data": data if data is not None else {}
    }
    if extra and isinstance(extra, dict):
        payload.update(extra)
    return Response(payload, status=status_code)


def api_error(message="An error occurred.", code="ERROR", details=None, status_code=status.HTTP_400_BAD_REQUEST):
    """
    Produce a consistent error response payload.
    """
    payload = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details if details is not None else {}
        }
    }
    return Response(payload, status=status_code)
