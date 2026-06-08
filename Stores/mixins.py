from rest_framework.exceptions import NotFound, ValidationError

from Accounts.mixins import ClientContextMixin
from Stores.models import Store


class StoreContextMixin(ClientContextMixin):
    """Token + client + store context from X-Client-Id and X-Store-Id."""

    def check_permissions(self, request):
        if request.user and request.user.is_authenticated:
            self._resolve_client_membership(request)
            request.store = self._resolve_store(request)
        super(ClientContextMixin, self).check_permissions(request)

    def _resolve_store(self, request):
        store_id = request.META.get("HTTP_X_STORE_ID") or request.query_params.get("store_id")
        if not store_id:
            raise ValidationError({
                "store_id": "X-Store-Id header (or store_id query param) is required.",
            })

        store = Store.objects.filter(
            id=store_id,
            client=request.client,
            is_active=True,
        ).first()
        if not store:
            raise NotFound("Store not found or you do not have access.")
        return store
