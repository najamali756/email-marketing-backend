import logging
from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from Accounts.models import Store
from EmailMarketing.Views.base import StoreAuthenticatedMixin
from shopify_integration.models import EmailVerifier, DEFAULT_VERIFIER_DOMAINS

logger = logging.getLogger(__name__)


class EmailVerifierView(StoreAuthenticatedMixin, APIView):
    """
    API for managing allowed email provider domains whitelist per store.
    GET: Returns domain whitelist for store. If store not in table, uses get_or_create to auto-backfill defaults.
    PUT / POST: Updates whitelist for store.
    """

    def _resolve_target_store(self, request):
        store = getattr(request, "store", None)
        if store:
            return store
        store_id = request.query_params.get("store_id") or (request.data.get("store_id") if hasattr(request, "data") else None)
        if store_id:
            return Store.objects.filter(id=store_id, is_active=True).first()
        return None

    def get(self, request):
        store = self._resolve_target_store(request)
        if not store:
            return Response(
                {"status": "error", "message": "store_id is required or store could not be resolved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            verifier, created = EmailVerifier.objects.get_or_create(
                store=store,
                defaults={"domains": list(DEFAULT_VERIFIER_DOMAINS)}
            )
            if not verifier.domains:
                verifier.domains = list(DEFAULT_VERIFIER_DOMAINS)
                verifier.save(update_fields=["domains"])

            return Response({
                "status": "success",
                "store_id": store.id,
                "domains": verifier.domains or [],
                "total": len(verifier.domains or []),
            }, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.warning(f"[EMAIL VERIFIER GET] {exc} (Table might not be migrated yet). Returning defaults.")
            return Response({
                "status": "fallback",
                "store_id": store.id,
                "domains": list(DEFAULT_VERIFIER_DOMAINS),
                "total": len(DEFAULT_VERIFIER_DOMAINS),
                "message": "Showing default domains (database migration pending).",
            }, status=status.HTTP_200_OK)

    def put(self, request):
        store = self._resolve_target_store(request)
        if not store:
            return Response(
                {"status": "error", "message": "store_id is required or store could not be resolved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = request.data or {}
        new_domains = data.get("domains")

        if not isinstance(new_domains, list):
            return Response(
                {"status": "error", "message": "'domains' must be a list of domain strings"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Normalize and deduplicate domains
        cleaned = sorted(list({
            str(d).strip().lower().lstrip("@.").rstrip("/")
            for d in new_domains
            if str(d).strip() and "." in str(d)
        }))

        try:
            verifier, created = EmailVerifier.objects.get_or_create(
                store=store,
                defaults={"domains": cleaned}
            )
            if not created:
                verifier.domains = cleaned
                verifier.save(update_fields=["domains"])

            # Invalidate cache for this store
            cache.delete(f"email_verifier_domains_{store.id}")
            cache.delete("email_verifier_domains")

            return Response({
                "status": "success",
                "store_id": store.id,
                "domains": verifier.domains,
                "total": len(verifier.domains),
                "message": "Allowed email domains updated successfully for store.",
            }, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"[EMAIL VERIFIER PUT ERROR] {exc}")
            return Response(
                {"status": "error", "message": f"Could not update domains: {str(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request):
        return self.put(request)
