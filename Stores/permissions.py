from rest_framework.permissions import BasePermission


class HasStoreContext(BasePermission):
    def has_permission(self, request, view):
        return getattr(request, "store", None) is not None
