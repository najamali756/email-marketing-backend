from rest_framework.permissions import BasePermission

from Accounts.models import UserRoleEnum


class IsClientAdmin(BasePermission):
    def has_permission(self, request, view):
        if request.user and request.user.is_superuser:
            return True
        membership = getattr(request, "client_membership", None)
        return bool(membership and membership.role == UserRoleEnum.admin.value)
