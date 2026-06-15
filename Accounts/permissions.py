from rest_framework.permissions import BasePermission


class HasStoreContext(BasePermission):
    def has_permission(self, request, view):
        return getattr(request, "store", None) is not None


class IsClientAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or user.is_superuser:
            return True
        return user.user_type == "admin"
