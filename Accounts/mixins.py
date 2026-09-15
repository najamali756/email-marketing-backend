from django.core.cache import cache
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated

from Accounts.models import Client, ClientUser, Store


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
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            token_key = auth_header[6:].strip() if auth_header.startswith("Token ") else None
            cached_auth = cache.get(f"auth_token_{token_key}") if token_key else None
            if cached_auth:
                request.user, request.auth = cached_auth
            else:
                try:
                    auth_res = TokenAuthentication().authenticate(request)
                    if auth_res:
                        request.user, request.auth = auth_res
                        if token_key:
                            cache.set(f"auth_token_{token_key}", auth_res, 120)
                except Exception:
                    pass

        if not request.user or not request.user.is_authenticated:
            request.client = None
            return None

        user = request.user
        client_id = request.META.get("HTTP_X_CLIENT_ID") or request.query_params.get("client_id")

        cache_key = f"auth_client_{user.id}_{client_id}"
        try:
            cached_client = cache.get(cache_key)
            if cached_client is not None:
                request.client = cached_client
                return None
        except Exception:
            pass

        if user.is_staff or user.is_superuser:
            if client_id:
                client = Client.objects.filter(id=client_id, is_active=True).first()
            else:
                client = Client.objects.filter(is_active=True).first()
        else:
            memberships = ClientUser.objects.filter(user=user, is_active=True, client__is_active=True)
            if client_id:
                client = Client.objects.filter(id=client_id, is_active=True).first()
                if client not in [m.client for m in memberships]:
                    client = None
            else:
                first_membership = memberships.first()
                client = first_membership.client if first_membership else None

        request.client = client
        try:
            cache.set(cache_key, client, 120)
        except Exception:
            pass
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

        cache_key = f"auth_store_{user.id}_{request.client.id}_{store_id}"
        try:
            cached_store = cache.get(cache_key)
            if cached_store is not None:
                return cached_store
        except Exception:
            pass

        if user.is_staff or user.is_superuser or user.user_type == 'admin':
            if store_id:
                store = Store.objects.filter(id=store_id, client=request.client, is_active=True).first()
            else:
                store = Store.objects.filter(client=request.client, is_active=True).first()
        else:
            assigned = user.assigned_stores.filter(client=request.client, is_active=True)
            if store_id:
                store = assigned.filter(id=store_id).first()
            else:
                store = assigned.first()

        try:
            cache.set(cache_key, store, 120)
        except Exception:
            pass
        return store
