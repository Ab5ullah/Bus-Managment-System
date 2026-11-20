"""
Role-Based Access Control (RBAC) permission classes.
"""
from rest_framework.permissions import BasePermission
from common.constants import UserRole


class IsPassenger(BasePermission):
    """Allows access only to authenticated passenger users."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == UserRole.PASSENGER)


class IsCounterAgent(BasePermission):
    """Allows access only to counter agents."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == UserRole.COUNTER_AGENT)


class IsOperator(BasePermission):
    """Allows access to bus operators and super admins."""
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and
            (request.user.role in (UserRole.OPERATOR, UserRole.SUPER_ADMIN) or request.user.is_staff)
        )


class IsConductor(BasePermission):
    """Allows access to conductors and drivers."""
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and
            (request.user.role in (UserRole.CONDUCTOR, UserRole.SUPER_ADMIN) or request.user.is_staff)
        )


class IsAdminOrSuperUser(BasePermission):
    """Allows access only to super admins and staff."""
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and
            (request.user.role == UserRole.SUPER_ADMIN or request.user.is_staff or request.user.is_superuser)
        )


class IsOperatorOrAgent(BasePermission):
    """Allows access to operators, counter agents, or super admins."""
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and
            (request.user.role in (UserRole.OPERATOR, UserRole.COUNTER_AGENT, UserRole.SUPER_ADMIN) or request.user.is_staff)
        )
