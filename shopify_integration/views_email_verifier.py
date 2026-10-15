import logging
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from Accounts.models import Store
from EmailMarketing.Views.base import StoreAuthenticatedMixin
from shopify_integration.models import EmailVerifier, DEFAULT_VERIFIER_DOMAINS

logger = logging.getLogger(__name__)


class EmailVerifierView(StoreAuthenticatedMixin, APIView):
    """
    API for managing allowed email provider domains whitelist and active toggle per store.
    Direct DB access, NO caching to prevent stale state across workers.
    """

    def _resolve_target_store(self, request):
        # Explicit store_id from query or body takes absolute priority
        store_id = request.query_params.get("store_id")
        if not store_id and hasattr(request, "data") and isinstance(request.data, dict):
            store_id = request.data.get("store_id")

        if store_id:
            try:
                s = Store.objects.filter(id=int(store_id), is_active=True).first()
                if s:
                    return s
            except (ValueError, TypeError):
                pass

        return getattr(request, "store", None)

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
                defaults={"domains": list(DEFAULT_VERIFIER_DOMAINS), "is_active": True}
            )
            if not verifier.domains:
                verifier.domains = list(DEFAULT_VERIFIER_DOMAINS)
                try:
                    verifier.save(update_fields=["domains"])
                except Exception:
                    verifier.save()

            is_active_val = getattr(verifier, "is_active", True)

            return Response({
                "status": "success",
                "store_id": store.id,
                "is_active": is_active_val,
                "domains": verifier.domains or [],
                "total": len(verifier.domains or []),
            }, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.warning(f"[EMAIL VERIFIER GET] {exc}. Returning defaults.")
            return Response({
                "status": "fallback",
                "store_id": store.id,
                "is_active": True,
                "domains": list(DEFAULT_VERIFIER_DOMAINS),
                "total": len(DEFAULT_VERIFIER_DOMAINS),
                "message": "Showing default domains.",
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
        is_active_val = data.get("is_active")

        cleaned = None
        if new_domains is not None:
            if not isinstance(new_domains, list):
                return Response(
                    {"status": "error", "message": "'domains' must be a list of domain strings"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cleaned = sorted(list({
                str(d).strip().lower().lstrip("@.").rstrip("/")
                for d in new_domains
                if str(d).strip() and "." in str(d)
            }))

        try:
            defaults = {
                "domains": cleaned if cleaned is not None else list(DEFAULT_VERIFIER_DOMAINS),
                "is_active": bool(is_active_val) if is_active_val is not None else True
            }

            verifier, created = EmailVerifier.objects.get_or_create(
                store=store,
                defaults=defaults
            )

            if not created:
                update_fields = []
                if cleaned is not None:
                    verifier.domains = cleaned
                    update_fields.append("domains")
                if is_active_val is not None:
                    verifier.is_active = bool(is_active_val)
                    update_fields.append("is_active")

                if update_fields:
                    try:
                        verifier.save(update_fields=update_fields)
                    except Exception:
                        verifier.save()

            print(f"[EMAIL VERIFIER PUT] Store {store.id} updated in DB: is_active={verifier.is_active}, domains={len(verifier.domains or [])}")

            return Response({
                "status": "success",
                "store_id": store.id,
                "is_active": getattr(verifier, "is_active", True),
                "domains": verifier.domains,
                "total": len(verifier.domains),
                "message": "Allowed email domains and status updated successfully for store.",
            }, status=status.HTTP_200_OK)
        except Exception as exc:
            logger.error(f"[EMAIL VERIFIER PUT ERROR] {exc}")
            return Response(
                {"status": "error", "message": f"Could not update verifier: {str(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def post(self, request):
        return self.put(request)
