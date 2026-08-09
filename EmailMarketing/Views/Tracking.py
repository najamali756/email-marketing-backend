import base64
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from rest_framework.views import APIView

from EmailMarketing.models import EmailCampaignRecipient, EmailRecipientStatusEnum


# 1x1 transparent PNG image binary data
TRANSPARENT_1X1_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class EmailOpenTrackingView(APIView):
    """
    1x1 Transparent PNG Tracking Pixel Endpoint.
    GET /emailMarketing/track/open?token=<tracking_token>
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        token = request.query_params.get("token")
        if token:
            try:
                recipient = (
                    EmailCampaignRecipient.objects.select_related("campaign")
                    .filter(tracking_token=token)
                    .first()
                )
                if recipient:
                    now = timezone.now()
                    if not recipient.opened_at:
                        recipient.opened_at = now
                        if recipient.status in [EmailRecipientStatusEnum.sent.value, EmailRecipientStatusEnum.pending.value]:
                            recipient.status = EmailRecipientStatusEnum.opened.value
                        recipient.save(update_fields=["opened_at", "status", "updated_at"])

                        campaign = recipient.campaign
                        campaign.open_count = EmailCampaignRecipient.objects.filter(
                            campaign=campaign, opened_at__isnull=False
                        ).count()
                        campaign.save(update_fields=["open_count", "updated_at"])
            except Exception as e:
                pass

        # Return 1x1 transparent PNG pixel image with no-cache headers
        response = HttpResponse(TRANSPARENT_1X1_PNG, content_type="image/png")
        response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


class EmailClickTrackingView(APIView):
    """
    Link Click Tracking Redirect Endpoint.
    GET /emailMarketing/track/click?token=<tracking_token>&url=<destination_url>
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        token = request.query_params.get("token")
        target_url = request.query_params.get("url", "/")

        if token:
            try:
                recipient = (
                    EmailCampaignRecipient.objects.select_related("campaign")
                    .filter(tracking_token=token)
                    .first()
                )
                if recipient:
                    now = timezone.now()
                    recipient.clicked_at = now
                    recipient.status = EmailRecipientStatusEnum.clicked.value
                    if not recipient.opened_at:
                        recipient.opened_at = now
                    recipient.save(update_fields=["clicked_at", "opened_at", "status", "updated_at"])

                    campaign = recipient.campaign
                    campaign.click_count = EmailCampaignRecipient.objects.filter(
                        campaign=campaign, clicked_at__isnull=False
                    ).count()
                    if not recipient.opened_at:
                        campaign.open_count = EmailCampaignRecipient.objects.filter(
                            campaign=campaign, opened_at__isnull=False
                        ).count()
                    campaign.save(update_fields=["click_count", "open_count", "updated_at"])
            except Exception as e:
                pass

        return HttpResponseRedirect(target_url)
