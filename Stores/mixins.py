from rest_framework.exceptions import NotFound, ValidationError

from Accounts.mixins import ClientContextMixin
from Stores.models import Store


class StoreContextMixin(ClientContextMixin):
    """Token + client + store context from X-Client-Id and X-Store-Id (bypassed auth)."""

    def check_permissions(self, request):
        self._resolve_client_membership(request)
        request.store = self._resolve_store(request)

    def _resolve_store(self, request):
        store_id = request.META.get("HTTP_X_STORE_ID") or request.query_params.get("store_id")
        if not store_id:
            # Fallback to the first active store of the client
            store = Store.objects.filter(client=request.client, is_active=True).first()
            if not store:
                raise NotFound("No active Store found. Please seed.")
            return store

        store = Store.objects.filter(
            id=store_id,
            client=request.client,
            is_active=True,
        ).first()
        if not store:
            raise NotFound("Store not found or you do not have access.")
        return store
