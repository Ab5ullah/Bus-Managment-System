"""
Custom exception classes and standardized exception handler for DRF.
"""
import logging
from rest_framework.views import exception_handler
from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    ValidationError,
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    NotFound,
    MethodNotAllowed,
    Throttled,
)
from django.http import Http404
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

logger = logging.getLogger(__name__)


class BusinessLogicError(APIException):
    """Raised when a domain/business rule is violated."""
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "A business rule constraint was violated."
    default_code = "BUSINESS_RULE_VIOLATION"


class ResourceConflictError(APIException):
    """Raised when there is a state conflict (e.g. seat lock collision)."""
    status_code = status.HTTP_409_CONFLICT
    default_detail = "A resource conflict occurred."
    default_code = "RESOURCE_CONFLICT"


class SeatUnavailableError(ResourceConflictError):
    """Raised when requested seats are already booked or locked."""
    default_detail = "One or more requested seats are no longer available."
    default_code = "SEAT_UNAVAILABLE"


class SeatLockExpiredError(APIException):
    """Raised when a temporary seat lock has expired."""
    status_code = status.HTTP_410_GONE
    default_detail = "Seat lock has expired. Please select seats again."
    default_code = "SEAT_LOCK_EXPIRED"


def custom_exception_handler(exc, context):
    """
    Standardized DRF Exception Handler.
    Returns:
    {
        "success": False,
        "error": {
            "code": "<ERROR_CODE>",
            "message": "<Human readable description>",
            "details": { ... or [ ... ] }
        }
    }
    """
    # Call REST framework's default exception handler first to get standard response
    response = exception_handler(exc, context)

    if response is not None:
        error_code = "ERROR"
        message = "An error occurred while processing the request."
        details = response.data

        if isinstance(exc, ValidationError):
            error_code = "VALIDATION_ERROR"
            message = "Validation failed for one or more fields."
        elif isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
            error_code = "AUTHENTICATION_FAILED"
            message = getattr(exc, 'detail', "Authentication credentials were not provided or are invalid.")
            if isinstance(message, dict) and 'detail' in message:
                message = str(message['detail'])
            details = {}
        elif isinstance(exc, (PermissionDenied, DjangoPermissionDenied)):
            error_code = "PERMISSION_DENIED"
            message = getattr(exc, 'detail', "You do not have permission to perform this action.")
            if isinstance(message, dict) and 'detail' in message:
                message = str(message['detail'])
            details = {}
        elif isinstance(exc, (NotFound, Http404)):
            error_code = "NOT_FOUND"
            message = "The requested resource was not found."
            details = {}
        elif isinstance(exc, MethodNotAllowed):
            error_code = "METHOD_NOT_ALLOWED"
            message = f"Method '{context.get('request').method}' not allowed."
            details = {}
        elif isinstance(exc, Throttled):
            error_code = "RATE_LIMIT_EXCEEDED"
            wait_time = getattr(exc, 'wait', None)
            message = f"Request was throttled. Expected available in {wait_time} seconds." if wait_time else "Too many requests."
            details = {"wait_seconds": wait_time}
        elif hasattr(exc, 'default_code'):
            error_code = str(exc.default_code).upper()
            if hasattr(exc, 'detail'):
                message = str(exc.detail)

        # Standardize the payload
        response.data = {
            "success": False,
            "error": {
                "code": error_code,
                "message": str(message),
                "details": details if details is not None else {}
            }
        }
        return response

    # Handle unhandled server exceptions (500)
    logger.exception("Unhandled Server Exception: %s", exc)
    from rest_framework.response import Response
    return Response(
        {
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred on the server.",
                "details": {}
            }
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )
