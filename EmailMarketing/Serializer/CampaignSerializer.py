from rest_framework import serializers

from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient


class EmailCampaignSerializer(serializers.ModelSerializer):
    open_rate = serializers.SerializerMethodField()
    click_rate = serializers.SerializerMethodField()
    audience_name = serializers.CharField(source="segment.name", read_only=True, default=None)

    class Meta:
        model = EmailCampaign
        fields = (
            "id", "name", "subject", "preview_text", "template", "segment", "status",
            "campaign_type", "scheduled_at", "sent_at", "from_name", "html_content",
            "total_recipients", "sent_count", "failed_count", "skipped_count",
            "open_count", "click_count", "revenue", "open_rate", "click_rate",
            "audience_name", "wizard_step", "created_at", "updated_at",
        )
        read_only_fields = (
            "sent_at", "total_recipients", "sent_count", "failed_count",
            "skipped_count", "open_count", "click_count", "revenue", "created_at", "updated_at",
        )

    def get_open_rate(self, obj):
        return round((obj.open_count / obj.sent_count) * 100, 1) if obj.sent_count else 0

    def get_click_rate(self, obj):
        return round((obj.click_count / obj.sent_count) * 100, 1) if obj.sent_count else 0


class EmailCampaignCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailCampaign
        fields = (
            "id", "name", "subject", "preview_text", "template", "segment",
            "campaign_type", "scheduled_at", "from_name", "html_content", "wizard_step",
        )
        read_only_fields = ("id",)


class SendCampaignSerializer(serializers.Serializer):
    test_email = serializers.EmailField(required=False, allow_null=True)
    personalization = serializers.JSONField(required=False, default=dict)


class CampaignRecipientSerializer(serializers.ModelSerializer):
    contact_name = serializers.SerializerMethodField()

    class Meta:
        model = EmailCampaignRecipient
        fields = (
            "id", "email", "status", "sent_at", "opened_at", "clicked_at",
            "error_message", "contact_name",
        )

    def get_contact_name(self, obj):
        parts = [obj.contact.first_name, obj.contact.last_name]
        return " ".join(part for part in parts if part).strip()
