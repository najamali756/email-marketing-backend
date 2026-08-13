from decimal import Decimal
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient, EmailRecipientStatusEnum


class ShopifyPixelEventIngestionView(APIView):
    """
    Open Public Web Pixel & Storefront Ingestion API Endpoint (No Auth required).
    POST /shopify/events/track
    Payload:
    {
        "execution_id": "uuid-token",
        "event_name": "checkout_completed" | "product_added_to_cart" | "page_viewed",
        "order_id": "1001",
        "order_total": 250.00,
        "discount_code": "SUMMER50",
        "email": "shopper@example.com"
    }
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def options(self, request, *args, **kwargs):
        response = JsonResponse({"status": "ok"})
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    def post(self, request, *args, **kwargs):
        data = request.data or {}
        execution_id = data.get("execution_id") or request.query_params.get("execution_id")
        event_name = data.get("event_name", "page_viewed")
        order_id = data.get("order_id")
        discount_code = data.get("discount_code")

        try:
            order_total = Decimal(str(data.get("order_total", 0) or 0))
        except Exception:
            order_total = Decimal("0.00")

        if execution_id:
            try:
                recipient = (
                    EmailCampaignRecipient.objects.select_related("campaign")
                    .filter(tracking_token=execution_id)
                    .first()
                )
                if recipient:
                    now = timezone.now()
                    campaign = recipient.campaign

                    if event_name in ["checkout_completed", "purchase_completed"]:
                        recipient.converted_at = now
                        recipient.clicked_at = recipient.clicked_at or now
                        recipient.opened_at = recipient.opened_at or now
                        recipient.order_id = order_id or recipient.order_id
                        recipient.order_total = order_total
                        recipient.discount_code = discount_code or recipient.discount_code
                        recipient.status = EmailRecipientStatusEnum.purchased.value
                        recipient.save(update_fields=["converted_at", "clicked_at", "opened_at", "order_id", "order_total", "discount_code", "status", "updated_at"])

                        from EmailMarketing.Views.Campaigns import recalculate_campaign_stats
                        recalculate_campaign_stats(campaign)
                    
                    elif event_name in ["checkout_started", "checkout_initiated"]:
                        recipient.clicked_at = recipient.clicked_at or now
                        recipient.opened_at = recipient.opened_at or now
                        recipient.checkout_started_count = (recipient.checkout_started_count or 0) + 1
                        if recipient.status in [EmailRecipientStatusEnum.sent.value, EmailRecipientStatusEnum.opened.value, EmailRecipientStatusEnum.clicked.value, EmailRecipientStatusEnum.added_to_cart.value]:
                            recipient.status = EmailRecipientStatusEnum.checkout_started.value
                        recipient.save(update_fields=["clicked_at", "opened_at", "checkout_started_count", "status", "updated_at"])

                        from EmailMarketing.Views.Campaigns import recalculate_campaign_stats
                        recalculate_campaign_stats(campaign)

                    elif event_name == "product_added_to_cart":
                        recipient.clicked_at = recipient.clicked_at or now
                        recipient.opened_at = recipient.opened_at or now
                        recipient.add_to_cart_count = (recipient.add_to_cart_count or 0) + 1
                        if data.get("cart_total"):
                            try:
                                recipient.cart_total = Decimal(str(data.get("cart_total")))
                            except Exception:
                                pass
                        if recipient.status in [EmailRecipientStatusEnum.sent.value, EmailRecipientStatusEnum.opened.value, EmailRecipientStatusEnum.clicked.value]:
                            recipient.status = EmailRecipientStatusEnum.added_to_cart.value
                        recipient.save(update_fields=["clicked_at", "opened_at", "add_to_cart_count", "cart_total", "status", "updated_at"])

                        from EmailMarketing.Views.Campaigns import recalculate_campaign_stats
                        recalculate_campaign_stats(campaign)

                    elif event_name == "page_viewed":
                        recipient.opened_at = recipient.opened_at or now
                        recipient.page_view_count = (recipient.page_view_count or 0) + 1
                        if recipient.status == EmailRecipientStatusEnum.sent.value:
                            recipient.status = EmailRecipientStatusEnum.opened.value
                        recipient.save(update_fields=["opened_at", "page_view_count", "status", "updated_at"])

                        from EmailMarketing.Views.Campaigns import recalculate_campaign_stats
                        recalculate_campaign_stats(campaign)

            except Exception as e:
                pass

        response = JsonResponse({"status": "tracked", "execution_id": execution_id})
        response["Access-Control-Allow-Origin"] = "*"
        return response
