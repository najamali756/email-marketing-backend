from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.models import EmailCampaignRecipient, EmailUnsubscribe
from shopify_integration.models import ShopifySettings
from shopify_integration.sync import update_customer_marketing_on_shopify_in_background


class EmailUnsubscribeView(APIView):
    authentication_classes = []
    permission_classes = []

    def handle_unsubscribe(self, request):
        token = request.query_params.get("token") or request.data.get("token")
        if not token:
            return Response({"detail": "Token is required."}, status=400)

        recipient = (
            EmailCampaignRecipient.objects.select_related("campaign", "contact", "campaign__store")
            .filter(tracking_token=token)
            .first()
        )
        if not recipient:
            return Response({"detail": "Invalid unsubscribe link or token."}, status=404)

        store = recipient.campaign.store
        email = recipient.email

        # 1. Save record in EmailUnsubscribe
        EmailUnsubscribe.objects.get_or_create(
            store=store,
            email=email,
            defaults={"contact": recipient.contact},
        )

        # 2. Update local Contact marketing status
        contact = recipient.contact
        if contact:
            contact.accept_email_marketing = False
            contact.accept_email_marketing_at = timezone.now()
            contact.save(update_fields=["accept_email_marketing", "accept_email_marketing_at"])

            # 3. Trigger Shopify background unsubscribe if store has Shopify integration & contact has external_id
            if contact.external_id:
                settings_obj = ShopifySettings.objects.filter(store=store).first()
                if settings_obj and settings_obj.shopify_access_token:
                    update_customer_marketing_on_shopify_in_background(
                        settings_obj.shop_url,
                        settings_obj.shopify_access_token,
                        store,
                        contact.external_id,
                        accepts_marketing=False
                    )

        # 4. Update recipient timestamp and status
        now = timezone.now()
        recipient.unsubscribed_at = now
        from EmailMarketing.models import EmailRecipientStatusEnum
        recipient.status = EmailRecipientStatusEnum.unsubscribed.value
        recipient.save(update_fields=["unsubscribed_at", "status", "updated_at"])

        # 5. Update campaign aggregate unsubscribe_count
        campaign = recipient.campaign
        campaign.unsubscribe_count = EmailCampaignRecipient.objects.filter(
            campaign=campaign, unsubscribed_at__isnull=False
        ).count()
        campaign.save(update_fields=["unsubscribe_count", "updated_at"])

        return Response({"detail": f"Unsubscribed '{email}' successfully from {store.name}."})

    def get(self, request):
        return self.handle_unsubscribe(request)

    def post(self, request):
        return self.handle_unsubscribe(request)
