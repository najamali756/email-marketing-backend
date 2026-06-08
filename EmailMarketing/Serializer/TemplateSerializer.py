from rest_framework import serializers

from EmailMarketing.models import EmailBrandSettings, EmailTemplate


class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = (
            "id", "name", "category", "subject", "preview_text", "html_content",
            "editor_json", "is_system", "is_active", "created_at", "updated_at",
        )


class EmailBrandSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailBrandSettings
        fields = (
            "id", "store_name", "store_logo_url", "brand_color", "default_from_name",
            "sender_email", "notify_campaign_sent", "notify_weekly_reports",
            "notify_new_subscriber", "created_at", "updated_at",
        )
