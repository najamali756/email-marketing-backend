from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.BusinessLogic.BulkEmailSender import BulkEmailSender
from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient, EmailCampaignStatusEnum
from EmailMarketing.Serializer.CampaignSerializer import (
    CampaignRecipientSerializer,
    EmailCampaignCreateSerializer,
    EmailCampaignSerializer,
    SendCampaignSerializer,
)
from EmailMarketing.Views.base import StoreAuthenticatedMixin


class EmailCampaignListCreateView(StoreAuthenticatedMixin, ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return EmailCampaignCreateSerializer
        return EmailCampaignSerializer

    def get_queryset(self):
        return EmailCampaign.objects.filter(store=self.request.store).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(store=self.request.store, status=EmailCampaignStatusEnum.draft.value)


class EmailCampaignDetailView(StoreAuthenticatedMixin, RetrieveUpdateAPIView):
    serializer_class = EmailCampaignSerializer

    def get_queryset(self):
        return EmailCampaign.objects.filter(store=self.request.store)


class BuildCampaignAudienceView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        total = BulkEmailSender(campaign.id).build_recipients()

        from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver
        filter_config = campaign.segment.filter_config if campaign.segment else {}
        breakdown = AudienceResolver(request.store).estimate_breakdown(filter_config)

        return Response({
            "campaign_id": campaign.id,
            "total_recipients": total,
            "audience_breakdown": breakdown,
        })


class SendCampaignView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SendCampaignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if serializer.validated_data.get("test_email"):
            BulkEmailSender(campaign.id).send_test_email(serializer.validated_data["test_email"])
            return Response({"detail": "Test email sent."})

        if campaign.total_recipients == 0:
            total = BulkEmailSender(campaign.id).build_recipients()
            if total == 0:
                return Response({"detail": "No recipients found."}, status=400)

        if campaign.status == EmailCampaignStatusEnum.sending.value:
            return Response({"detail": "Campaign is already sending."}, status=400)

        BulkEmailSender(campaign.id).send_async()
        return Response({
            "detail": "Campaign send started.",
            "campaign_id": campaign.id,
            "status": EmailCampaignStatusEnum.sending.value,
        })


class CampaignRecipientsView(StoreAuthenticatedMixin, APIView):
    def get(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        recipients = EmailCampaignRecipient.objects.filter(campaign=campaign).select_related("contact")[:100]
        return Response({
            "results": CampaignRecipientSerializer(recipients, many=True).data,
            "total_recipients": campaign.total_recipients,
        })
