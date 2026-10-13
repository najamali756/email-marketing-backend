import base64
import logging
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from Accounts.models import Store
from EmailMarketing.Views.base import StoreAuthenticatedMixin
from automation.models import Automation, NotificationSend
from automation.serializers import (
    AutomationDetailSerializer,
    AutomationListSerializer,
    NotificationSendListSerializer,
    TriggerNotificationSerializer,
)
from automation.services import (
    dispatch_automation_email,
    notify_external_webhook,
)

logger = logging.getLogger(__name__)

TRANSPARENT_1X1_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class AutomationViewSet(StoreAuthenticatedMixin, viewsets.ModelViewSet):
    """
    CRUD endpoints for Automations.
    GET    /automations/?store_id=<id>&search=<query>&source=<source>
    POST   /automations/
    GET    /automations/<id>/
    PUT    /automations/<id>/
    DELETE /automations/<id>/
    """
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = Automation.objects.select_related("store").annotate(
            annotated_total_sent=models.Count("sends"),
            annotated_total_opened=models.Count(
                "sends", filter=models.Q(sends__status="Opened")
            ),
        )
        store = getattr(self.request, "store", None)
        if store:
            qs = qs.filter(store=store)
        else:
            store_id = self.request.query_params.get("store_id")
            if store_id:
                qs = qs.filter(store_id=store_id)

        source = self.request.query_params.get("source")
        if source and source != "all":
            qs = qs.filter(source=source)

        is_active = self.request.query_params.get("is_active")
        if is_active is not None and is_active != "":
            qs = qs.filter(is_active=(is_active.lower() in ["true", "1"]))

        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                models.Q(name__icontains=search)
                | models.Q(external_name__icontains=search)
                | models.Q(subject__icontains=search)
                | models.Q(template_name__icontains=search)
            )

        return qs.order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "list":
            return AutomationListSerializer
        return AutomationDetailSerializer

    def perform_create(self, serializer):
        store = getattr(self.request, "store", None)
        if store:
            serializer.save(store=store)
        else:
            serializer.save()


class AutomationStatsView(StoreAuthenticatedMixin, APIView):
    """
    Aggregated statistical reports for the store automations dashboard.
    GET /automations/stats/?store_id=<id>
    """

    def get(self, request):
        store = getattr(request, "store", None)
        store_id = store.id if store else request.query_params.get("store_id")
        if not store_id:
            return Response(
                {"error": "store_id query param is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Single optimized aggregation for send metrics
        send_metrics = NotificationSend.objects.filter(store_id=store_id).aggregate(
            total_dispatched=models.Count("id"),
            total_sent=models.Count("id", filter=models.Q(status__in=["Sent", "Opened", "Clicked", "Recovered"])),
            total_opened=models.Count("id", filter=models.Q(status__in=["Opened", "Clicked", "Recovered"])),
            total_failed=models.Count("id", filter=models.Q(status__in=["Failed", "Bounced"])),
            total_recovered=models.Count("id", filter=models.Q(status="Recovered")),
        )

        total_dispatched = send_metrics.get("total_dispatched") or 0
        total_sent = send_metrics.get("total_sent") or 0
        total_opened = send_metrics.get("total_opened") or 0
        total_failed = send_metrics.get("total_failed") or 0
        total_recovered = send_metrics.get("total_recovered") or 0

        # Calculate recovered revenue from variables JSONField
        recovered_sends = NotificationSend.objects.filter(store_id=store_id, status="Recovered").values_list("variables", flat=True)
        total_recovered_revenue = 0.0
        currency = "PKR"
        for vars_dict in recovered_sends:
            if isinstance(vars_dict, dict):
                amt_str = vars_dict.get("recovered_amount") or "0"
                try:
                    total_recovered_revenue += float(str(amt_str).replace(",", ""))
                except (ValueError, TypeError):
                    pass
                if vars_dict.get("recovered_currency"):
                    currency = str(vars_dict.get("recovered_currency"))

        # Single optimized aggregation for automation counts
        workflow_metrics = Automation.objects.filter(store_id=store_id).aggregate(
            total=models.Count("id"),
            active=models.Count("id", filter=models.Q(is_active=True)),
        )

        total_automations = workflow_metrics.get("total") or 0
        active_automations = workflow_metrics.get("active") or 0

        open_rate = round((total_opened / total_dispatched) * 100, 1) if total_dispatched > 0 else 0.0
        sent_rate = round((total_sent / total_dispatched) * 100, 1) if total_dispatched > 0 else 0.0
        recovery_rate = round((total_recovered / total_sent) * 100, 1) if total_sent > 0 else 0.0

        return Response({
            "total_dispatched": total_dispatched,
            "total_sent": total_sent,
            "total_opened": total_opened,
            "total_failed": total_failed,
            "total_recovered": total_recovered,
            "total_recovered_revenue": round(total_recovered_revenue, 2),
            "currency": currency,
            "open_rate": open_rate,
            "sent_rate": sent_rate,
            "recovery_rate": recovery_rate,
            "active_automations": active_automations,
            "total_automations": total_automations,
        })


class NotificationSendHistoryView(StoreAuthenticatedMixin, APIView):
    """
    Paginated, searchable notification history log datatable.
    GET /automations/history/?store_id=<id>&search=<query>&status=<status>&automation_id=<id>&page=1
    """

    def get(self, request):
        store = getattr(request, "store", None)
        store_id = store.id if store else request.query_params.get("store_id")
        if not store_id:
            return Response(
                {"error": "store_id query param is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = NotificationSend.objects.select_related("automation", "store").filter(
            store_id=store_id
        )

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                models.Q(recipient_email__icontains=search)
                | models.Q(unique_key__icontains=search)
                | models.Q(automation__name__icontains=search)
            )

        status_filter = request.query_params.get("status")
        if status_filter and status_filter.lower() != "all":
            qs = qs.filter(status__iexact=status_filter)

        automation_id = request.query_params.get("automation_id")
        if automation_id:
            qs = qs.filter(automation_id=automation_id)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs.order_by("-created_at"), request)
        serializer = NotificationSendListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class TriggerNotificationView(APIView):
    """
    Third-party external trigger endpoint.
    POST /automations/trigger/
    Payload:
    {
      "notification_name": "ORDER_DISPATCHED",
      "unique_key": "#ORD-10928",
      "recipient_email": "customer@example.com",
      "variables": { "customer_name": "John Doe", "total": ".00" },
      "third_party_token": "<store_auth_key>"
    }
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        # 1. Resolve Auth Token from Authorization Header or Request Body
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1].strip()
        elif request.headers.get("X-Store-Token"):
            token = request.headers.get("X-Store-Token").strip()
        elif "third_party_token" in request.data:
            token = str(request.data.get("third_party_token")).strip()

        if not token:
            return Response(
                {
                    "status": "error",
                    "message": "Missing store authentication token in Authorization header or third_party_token field.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 2. Authenticate Store using third_party_token or api_key
        store_filter = models.Q(api_key=token)
        if hasattr(Store, "third_party_token"):
            store_filter |= models.Q(third_party_token=token)

        store = Store.objects.filter(store_filter, is_active=True).first()
        if not store:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid store authentication token or store is inactive.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 3. Validate Request Payload
        serializer = TriggerNotificationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"status": "error", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notif_name = serializer.validated_data["notification_name"].strip()
        unique_key = serializer.validated_data.get("unique_key", "").strip()
        recipient_email = serializer.validated_data["recipient_email"].strip()
        variables = serializer.validated_data.get("variables", {})

        # 4. Match Configured Automation for this Store (case-insensitive)
        automation = (
            Automation.objects.filter(store=store)
            .filter(
                models.Q(external_name__iexact=notif_name)
                | models.Q(name__iexact=notif_name)
            )
            .first()
        )

        if not automation:
            # Audit unrouted/missing automation record
            fail_record = NotificationSend.objects.create(
                store=store,
                automation=None,
                recipient_email=recipient_email,
                unique_key=unique_key,
                variables=variables,
                status="Failed",
                error_log=f"Automation with trigger name '{notif_name}' is not configured for this store.",
            )
            return Response(
                {
                    "status": "failed",
                    "message": f"Automation with trigger name '{notif_name}' is not configured for this store.",
                    "send_id": fail_record.id,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not automation.is_active:
            # Automation exists but is currently Paused/Inactive
            fail_record = NotificationSend.objects.create(
                store=store,
                automation=automation,
                recipient_email=recipient_email,
                unique_key=unique_key,
                variables=variables,
                status="Failed",
                error_log=f"Automation '{automation.name}' exists but is currently Paused (is_active=False).",
            )
            return Response(
                {
                    "status": "failed",
                    "message": f"Automation '{automation.name}' is currently paused/inactive. Please toggle it to 'Live' in your Automations dashboard.",
                    "send_id": fail_record.id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 5. Create Send Record in Pending Status
        send_record = NotificationSend.objects.create(
            store=store,
            automation=automation,
            recipient_email=recipient_email,
            unique_key=unique_key,
            variables=variables,
            status="Pending",
        )

        # 6. Entire email preparation, rendering, dispatch & webhook handled in background thread
        dispatch_automation_email(send_record.id)

        # 7. Non-blocking Immediate 200 OK Response
        return Response(
            {
                "status": "success",
                "message": "Notification received and scheduled for dispatch.",
                "send_id": send_record.id,
                "tracking_token": send_record.tracking_token,
            },
            status=status.HTTP_200_OK,
        )


class AutomationOpenTrackingView(APIView):
    """
    1x1 Transparent PNG Open Beacon Tracker.
    GET /automations/track/open?token=<tracking_token>
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        token = request.query_params.get("token") or request.GET.get("token")
        if token:
            token = str(token).strip()
            try:
                # 1. Anti-Bot / Anti-Scanner Check: Reject explicit prefetch requests
                purpose = (
                    request.headers.get("Purpose", "")
                    or request.headers.get("Sec-Purpose", "")
                    or request.headers.get("X-Purpose", "")
                    or request.META.get("HTTP_X_PURPOSE", "")
                ).lower()
                if "prefetch" in purpose or "preview" in purpose:
                    return self._pixel_response()

                # 2. Reject known automated security bots / scanners
                user_agent = (request.META.get("HTTP_USER_AGENT") or "").lower()
                scanner_bots = [
                    "barracuda",
                    "proofpoint",
                    "mimecast",
                    "virustotal",
                    "urlscan",
                    "crawler",
                    "spider",
                    "headless",
                    "python-requests",
                    "curl",
                    "wget",
                ]
                if any(bot in user_agent for bot in scanner_bots):
                    return self._pixel_response()

                record = (
                    NotificationSend.objects.select_related("automation", "store")
                    .filter(tracking_token=token)
                    .first()
                )
                if record:
                    now = timezone.now()
                    # Only mark as opened if not already opened
                    if not record.opened_at:
                        record.opened_at = now
                        if record.status in ["Sent", "Pending", "Waiting"]:
                            record.status = "Opened"
                        record.save(update_fields=["opened_at", "status", "updated_at"])

                        if (
                            record.automation
                            and getattr(record.automation, "send_callback", False)
                            and record.store
                            and record.store.callback_url
                        ):
                            cb_token = getattr(record.store, "third_party_token", None) or getattr(record.store, "api_key", None)
                            notify_external_webhook(
                                record.store.callback_url,
                                {
                                    "event": "email_opened",
                                    "unique_key": record.unique_key,
                                    "notification_name": (
                                        (record.automation.external_name or record.automation.name)
                                        if record.automation
                                        else "Notification"
                                    ),
                                    "recipient_email": record.recipient_email,
                                    "status": "Opened",
                                    "timestamp": now.isoformat(),
                                    "token": cb_token,
                                },
                            )
            except Exception as exc:
                logger.warning(f"Open tracking failed for token {token}: {exc}")

        return self._pixel_response()

    def head(self, request):
        # HEAD requests are inspection probes - return headers without mutating status
        return self._pixel_response()

    def _pixel_response(self):
        response = HttpResponse(TRANSPARENT_1X1_PNG, content_type="image/png")
        response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0, private"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        response["Access-Control-Allow-Origin"] = "*"
        return response



class BrowseAbandonmentEventView(APIView):
    """
    Storefront / Webhook endpoint to record a product browse event.
    POST /automations/browse-abandonment/
    Delegates directly to the unified handle_shopify_automation_event function.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        data = request.data or {}
        store_id = data.get("store_id")
        shop_domain = data.get("shop_domain") or data.get("shop")
        email = (data.get("email") or data.get("customer_email") or "").strip().lower()

        if not email or (not store_id and not shop_domain):
            return Response(
                {"status": "error", "message": "email and (store_id or shop_domain) are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        store = None
        if store_id:
            store = Store.objects.filter(id=store_id, is_active=True).first()
        if not store and shop_domain:
            from shopify_integration.models import ShopifySettings
            clean_shop = str(shop_domain).strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
            settings_obj = (
                ShopifySettings.objects.filter(shop_url__icontains=clean_shop).first() or
                ShopifySettings.objects.filter(shop_url=shop_domain).first()
            )
            if settings_obj:
                store = settings_obj.store

        if not store:
            return Response(
                {"status": "error", "message": "Store not found or inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Delegate directly to the central Shopify automation handler
        from automation.shopifyautomations import handle_shopify_automation_event
        handle_shopify_automation_event(store, "browse/abandonment", data)

        return Response(
            {
                "status": "queued",
                "message": "Browse event received and queued in Waiting status.",
            },
            status=status.HTTP_200_OK,
        )
