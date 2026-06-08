from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated

from Accounts.models import ClientUser


class AuthenticatedMixin:
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]


class ClientContextMixin(AuthenticatedMixin):
    """Token auth + active client from X-Client-Id header."""

    def check_permissions(self, request):
        if request.user and request.user.is_authenticated:
            self._resolve_client_membership(request)
        super().check_permissions(request)

    def _resolve_client_membership(self, request):
        if getattr(request, "client", None):
            return request.client_membership

        client_id = request.META.get("HTTP_X_CLIENT_ID") or request.query_params.get("client_id")
        if not client_id:
            raise ValidationError({
                "client_id": "X-Client-Id header (or client_id query param) is required.",
            })

        if request.user.is_superuser:
            from Accounts.models import Client
            client = Client.objects.filter(id=client_id, is_active=True).first()
            if not client:
                raise NotFound("Client not found.")
            request.client = client
            request.client_membership = None
            return None

        membership = (
            ClientUser.objects.select_related("client")
            .filter(
                user=request.user,
                client_id=client_id,
                is_active=True,
                client__is_active=True,
            )
            .first()
        )
        if not membership:
            raise NotFound("Client not found or you do not have access.")

        request.client = membership.client
        request.client_membership = membership
        return membership
