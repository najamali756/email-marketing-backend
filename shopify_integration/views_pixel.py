import logging
from decimal import Decimal
from datetime import timedelta
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from Accounts.models import Store
from shopify_integration.models import ShopifySettings
from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient, EmailRecipientStatusEnum
from EmailMarketing.Views.Campaigns import recalculate_campaign_stats

logger = logging.getLogger(__name__)


def clean_domain(domain):
    if not domain:
        return ""
    url_str = str(domain).strip().lower()
    url_str = url_str.replace("https://", "").replace("http://", "")
    if "admin.shopify.com/store/" in url_str:
        store_name = url_str.split("admin.shopify.com/store/")[1].split("/")[0].split("?")[0].strip()
        return f"{store_name}.myshopify.com"
    host = url_str.split("/")[0].strip()
    if host and "." not in host and not host.endswith(".myshopify.com"):
        host = f"{host}.myshopify.com"
    return host


class ShopifyPixelEventIngestionView(APIView):
    """
    Open Public Web Pixel & Storefront Ingestion API Endpoint (No Auth required).
    POST /shopify/events/track
    Payload:
    {
        "execution_id": "uuid-token",
        "email": "shopper@example.com",
        "customer_email": "shopper@example.com",
        "event_name": "checkout_completed" | "checkout_started" | "product_added_to_cart" | "page_viewed",
        "order_id": "1001",
        "order_total": 250.00,
        "discount_code": "SUMMER50",
        "shop_domain": "store.myshopify.com",
        "store_id": 28
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
        email = (data.get("email") or data.get("customer_email") or "").strip().lower()
        event_name = data.get("event_name", "page_viewed")
        order_id = data.get("order_id")
        discount_code = data.get("discount_code")
        shop_domain = data.get("shop_domain") or data.get("shop") or request.headers.get("X-Shopify-Shop-Domain")
        store_id = data.get("store_id")

        try:
            order_total = Decimal(str(data.get("order_total", 0) or 0))
        except Exception:
            order_total = Decimal("0.00")

        recipient = None

        # 1. Resolve recipient by execution_id (tracking_token) if provided
        if execution_id:
            try:
                recipient = (
                    EmailCampaignRecipient.objects.select_related("campaign", "campaign__store")
                    .filter(tracking_token=execution_id)
                    .first()
                )
            except Exception as e:
                logger.warning(f"[PIXEL TRACK] Lookup by tracking_token failed: {e}")

        # 2. If not found by execution_id, search by customer email for latest campaign
        if not recipient and email:
            try:
                store = None
                if store_id:
                    store = Store.objects.filter(id=store_id, is_active=True).first()
                elif shop_domain:
                    clean_shop = clean_domain(shop_domain)
                    settings_obj = (
                        ShopifySettings.objects.filter(shop_url__icontains=clean_shop).first() or
                        ShopifySettings.objects.filter(shop_url=shop_domain).first()
                    )
                    if settings_obj:
                        store = settings_obj.store

                candidates = EmailCampaignRecipient.objects.select_related("campaign", "campaign__store").filter(email__iexact=email)
                if store:
                    candidates = candidates.filter(campaign__store=store)

                # Fetch latest campaign recipient for this email
                recipient = candidates.order_by("-sent_at", "-created_at", "-id").first()
            except Exception as e:
                logger.warning(f"[PIXEL TRACK] Lookup by email '{email}' failed: {e}")

        if recipient:
            try:
                now = timezone.now()
                campaign = recipient.campaign

                # 3. 7-Day Attribution Window Check
                send_time = recipient.sent_at or (campaign.sent_at if campaign else None) or recipient.created_at
                if send_time:
                    elapsed = now - send_time
                    if elapsed.days > 7:
                        # Event occurred more than 7 days after campaign send -> Do not attribute to campaign
                        response = JsonResponse({
                            "status": "ignored",
                            "reason": "outside_7_day_attribution_window",
                            "days_elapsed": elapsed.days
                        })
                        response["Access-Control-Allow-Origin"] = "*"
                        return response

                # 4. Attribute Event within 7-day window
                if event_name in ["checkout_completed", "purchase_completed"]:
                    recipient.converted_at = now
                    recipient.clicked_at = recipient.clicked_at or now
                    recipient.opened_at = recipient.opened_at or now
                    recipient.order_id = str(order_id) if order_id else recipient.order_id
                    recipient.order_total = order_total
                    recipient.discount_code = discount_code or recipient.discount_code
                    recipient.status = EmailRecipientStatusEnum.purchased.value
                    recipient.save(update_fields=["converted_at", "clicked_at", "opened_at", "order_id", "order_total", "discount_code", "status", "updated_at"])

                    if campaign:
                        recalculate_campaign_stats(campaign)
                
                elif event_name in ["checkout_started", "checkout_initiated"]:
                    recipient.clicked_at = recipient.clicked_at or now
                    recipient.opened_at = recipient.opened_at or now
                    recipient.checkout_started_count = (recipient.checkout_started_count or 0) + 1
                    if recipient.status in [
                        EmailRecipientStatusEnum.sent.value,
                        EmailRecipientStatusEnum.opened.value,
                        EmailRecipientStatusEnum.clicked.value,
                        EmailRecipientStatusEnum.added_to_cart.value
                    ]:
                        recipient.status = EmailRecipientStatusEnum.checkout_started.value
                    recipient.save(update_fields=["clicked_at", "opened_at", "checkout_started_count", "status", "updated_at"])

                    if campaign:
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
                    if recipient.status in [
                        EmailRecipientStatusEnum.sent.value,
                        EmailRecipientStatusEnum.opened.value,
                        EmailRecipientStatusEnum.clicked.value
                    ]:
                        recipient.status = EmailRecipientStatusEnum.added_to_cart.value
                    recipient.save(update_fields=["clicked_at", "opened_at", "add_to_cart_count", "cart_total", "status", "updated_at"])

                    if campaign:
                        recalculate_campaign_stats(campaign)

                elif event_name == "page_viewed":
                    recipient.opened_at = recipient.opened_at or now
                    recipient.page_view_count = (recipient.page_view_count or 0) + 1
                    if recipient.status == EmailRecipientStatusEnum.sent.value:
                        recipient.status = EmailRecipientStatusEnum.opened.value
                    recipient.save(update_fields=["opened_at", "page_view_count", "status", "updated_at"])

                    if campaign:
                        recalculate_campaign_stats(campaign)

                response = JsonResponse({
                    "status": "tracked",
                    "campaign_id": campaign.id if campaign else None,
                    "recipient_id": recipient.id,
                    "event_name": event_name
                })
                response["Access-Control-Allow-Origin"] = "*"
                return response

            except Exception as e:
                logger.error(f"[PIXEL EVENT TRACKING ERROR] {e}", exc_info=True)

        response = JsonResponse({"status": "no_recipient_matched", "execution_id": execution_id, "email": email})
        response["Access-Control-Allow-Origin"] = "*"
        return response
