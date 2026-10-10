from rest_framework import serializers
from automation.models import Automation, NotificationSend


class AutomationListSerializer(serializers.ModelSerializer):
    total_sent = serializers.SerializerMethodField()
    total_opened = serializers.SerializerMethodField()
    open_rate = serializers.SerializerMethodField()

    class Meta:
        model = Automation
        fields = [
            "id",
            "name",
            "title",
            "source",
            "external_name",
            "template_name",
            "subject",
            "preview_text",
            "is_active",
            "send_callback",
            "total_sent",
            "total_opened",
            "open_rate",
            "created_at",
            "updated_at",
        ]

    def get_total_sent(self, obj):
        return getattr(obj, "annotated_total_sent", obj.sends.count())

    def get_total_opened(self, obj):
        return getattr(obj, "annotated_total_opened", obj.sends.filter(status="Opened").count())

    def get_open_rate(self, obj):
        sent = self.get_total_sent(obj)
        opened = self.get_total_opened(obj)
        if sent > 0:
            return round((opened / sent) * 100, 1)
        return 0.0


class AutomationDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Automation
        fields = [
            "id",
            "store",
            "name",
            "title",
            "source",
            "external_name",
            "template_name",
            "subject",
            "preview_text",
            "is_active",
            "send_callback",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class NotificationSendListSerializer(serializers.ModelSerializer):
    automation_name = serializers.CharField(source="automation.name", read_only=True, default="Custom / Unlinked")
    automation_id = serializers.IntegerField(source="automation.id", read_only=True, default=None)
    recovered_amount = serializers.SerializerMethodField()
    recovered_order_name = serializers.SerializerMethodField()
    recovered_at = serializers.SerializerMethodField()
    recovered_currency = serializers.SerializerMethodField()

    class Meta:
        model = NotificationSend
        fields = [
            "id",
            "recipient_email",
            "unique_key",
            "automation_id",
            "automation_name",
            "status",
            "variables",
            "error_log",
            "sent_at",
            "opened_at",
            "recovered_at",
            "recovered_amount",
            "recovered_order_name",
            "recovered_currency",
            "tracking_token",
            "created_at",
        ]

    def get_recovered_amount(self, obj):
        return (obj.variables or {}).get("recovered_amount")

    def get_recovered_order_name(self, obj):
        return (obj.variables or {}).get("recovered_order_name")

    def get_recovered_at(self, obj):
        return (obj.variables or {}).get("recovered_at")

    def get_recovered_currency(self, obj):
        return (obj.variables or {}).get("recovered_currency", "PKR")


class TriggerNotificationSerializer(serializers.Serializer):
    notification_name = serializers.CharField(max_length=255)
    unique_key = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    recipient_email = serializers.EmailField()
    variables = serializers.DictField(required=False, default=dict)
    third_party_token = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
