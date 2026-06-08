from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.models import EmailCampaignRecipient, EmailUnsubscribe


class EmailUnsubscribeView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        token = request.query_params.get("token")
        if not token:
            return Response({"detail": "Token is required."}, status=400)

        recipient = (
            EmailCampaignRecipient.objects.select_related("campaign", "contact", "campaign__store")
            .filter(tracking_token=token)
            .first()
        )
        if not recipient:
            return Response({"detail": "Invalid unsubscribe link."}, status=404)

        store = recipient.campaign.store
        EmailUnsubscribe.objects.get_or_create(
            store=store,
            email=recipient.email,
            defaults={"contact": recipient.contact},
        )

        contact = recipient.contact
        contact.accept_email_marketing = False
        contact.accept_email_marketing_at = timezone.now()
        contact.save(update_fields=["accept_email_marketing", "accept_email_marketing_at"])

        return Response({"detail": "You have been unsubscribed successfully."})
