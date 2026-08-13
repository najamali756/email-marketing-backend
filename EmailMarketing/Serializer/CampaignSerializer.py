from rest_framework import serializers

from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient


class EmailCampaignSerializer(serializers.ModelSerializer):
    open_rate = serializers.SerializerMethodField()
    click_rate = serializers.SerializerMethodField()
    conversion_rate = serializers.SerializerMethodField()
    aov = serializers.SerializerMethodField()
    rpe = serializers.SerializerMethodField()
    audience_name = serializers.CharField(source="segment.name", read_only=True, default=None)
    html_content = serializers.SerializerMethodField()

    class Meta:
        model = EmailCampaign
        fields = (
            "id", "name", "subject", "preview_text", "template", "segment", "status",
            "campaign_type", "scheduled_at", "sent_at", "from_name", "html_content",
            "total_recipients", "sent_count", "failed_count", "skipped_count",
            "open_count", "click_count", "page_view_count", "add_to_cart_count", "checkout_started_count",
            "unsubscribe_count", "orders_count", "revenue",
            "open_rate", "click_rate", "conversion_rate", "aov", "rpe",
            "audience_name", "wizard_step", "created_at", "updated_at",
        )
        read_only_fields = (
            "sent_at", "total_recipients", "sent_count", "failed_count",
            "skipped_count", "open_count", "click_count", "page_view_count", "add_to_cart_count", "checkout_started_count",
            "unsubscribe_count", "orders_count", "revenue", "created_at", "updated_at",
        )

    def get_open_rate(self, obj):
        return round((obj.open_count / obj.sent_count) * 100, 1) if obj.sent_count else 0

    def get_click_rate(self, obj):
        return round((obj.click_count / obj.sent_count) * 100, 1) if obj.sent_count else 0

    def get_conversion_rate(self, obj):
        return round((obj.orders_count / obj.sent_count) * 100, 1) if obj.sent_count else 0

    def get_aov(self, obj):
        return round(float(obj.revenue) / obj.orders_count, 2) if obj.orders_count else 0.0

    def get_rpe(self, obj):
        return round(float(obj.revenue) / obj.sent_count, 2) if obj.sent_count else 0.0

    def get_html_content(self, obj):
        if obj.html_content:
            return obj.html_content
        if obj.template and obj.template.html_content:
            return obj.template.html_content
        return ""


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
            "id", "email", "status", "sent_at", "opened_at", "clicked_at", "unsubscribed_at", "converted_at",
            "page_view_count", "add_to_cart_count", "checkout_started_count", "cart_total",
            "order_id", "order_total", "discount_code", "error_message", "contact_name",
        )

    def get_contact_name(self, obj):
        parts = [obj.contact.first_name, obj.contact.last_name]
        return " ".join(part for part in parts if part).strip()
