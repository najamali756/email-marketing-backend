from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated


class AuthenticatedMixin:
    authentication_classes = []
    permission_classes = []


class ClientContextMixin(AuthenticatedMixin):
    """Token auth + active client from X-Client-Id header (bypassed auth)."""

    def check_permissions(self, request):
        self._resolve_client_membership(request)
        super().check_permissions(request)

    def _resolve_client_membership(self, request):
        if getattr(request, "client", None):
            return None

        if not request.user or not request.user.is_authenticated:
            # If request is not authenticated, check token auth
            try:
                auth_res = TokenAuthentication().authenticate(request)
                if auth_res:
                    request.user, request.auth = auth_res
            except Exception:
                pass

        if not request.user or not request.user.is_authenticated:
            request.client = None
            return None

        user = request.user
        client_id = request.META.get("HTTP_X_CLIENT_ID") or request.query_params.get("client_id")
        from Accounts.models import Client, ClientUser

        if user.is_staff or user.is_superuser:
            # Staff has access to any client
            if client_id:
                client = Client.objects.filter(id=client_id, is_active=True).first()
            else:
                client = Client.objects.filter(is_active=True).first()
        else:
            # Admin/Operator can only access their linked clients
            memberships = ClientUser.objects.filter(user=user, is_active=True, client__is_active=True)
            if client_id:
                client = Client.objects.filter(id=client_id, is_active=True).first()
                if client not in [m.client for m in memberships]:
                    client = None
            else:
                # Default to first membership client
                first_membership = memberships.first()
                client = first_membership.client if first_membership else None

        request.client = client
        return None


class StoreContextMixin(ClientContextMixin):
    """Token + client + store context from X-Client-Id and X-Store-Id (bypassed auth)."""

    def check_permissions(self, request):
        self._resolve_client_membership(request)
        request.store = self._resolve_store(request)

    def _resolve_store(self, request):
        if not request.client:
            return None

        store_id = request.META.get("HTTP_X_STORE_ID") or request.query_params.get("store_id")
        user = request.user
        from Accounts.models import Store

        if user.is_staff or user.is_superuser or user.user_type == 'admin':
            # Staff/Admin has access to all active stores of their resolved client
            if store_id:
                store = Store.objects.filter(id=store_id, client=request.client, is_active=True).first()
            else:
                store = Store.objects.filter(client=request.client, is_active=True).first()
        else:
            # Operator has access only to their assigned stores belonging to the resolved client
            assigned = user.assigned_stores.filter(client=request.client, is_active=True)
            if store_id:
                store = assigned.filter(id=store_id).first()
            else:
                store = assigned.first()

        return store
