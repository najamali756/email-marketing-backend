import uuid
from enum import Enum
from django.db import models
from Accounts.models import Store, TimeStampedModel


class AutomationSourceEnum(Enum):
    shopify = "shopify"
    external = "external"


class NotificationSendStatusEnum(Enum):
    pending = "Pending"
    sent = "Sent"
    opened = "Opened"
    clicked = "Clicked"
    failed = "Failed"
    bounced = "Bounced"


class Automation(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="automations")
    name = models.CharField(max_length=255)
    title = models.CharField(max_length=255, blank=True, null=True)
    subject = models.CharField(max_length=500, blank=True, null=True)
    preview_text = models.CharField(max_length=500, blank=True, null=True)
    template_name = models.CharField(max_length=255, blank=True, null=True)
    source = models.CharField(max_length=50, default="shopify")  # 'shopify' | 'external'
    external_name = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    is_active = models.BooleanField(default=True)
    send_callback = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["store", "is_active"]),
            models.Index(fields=["store", "external_name"]),
            models.Index(fields=["store", "source"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.store.name})"


class NotificationSend(TimeStampedModel):
    automation = models.ForeignKey(
        Automation, on_delete=models.SET_NULL, null=True, blank=True, related_name="sends"
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="notification_sends")
    recipient_email = models.CharField(max_length=255, db_index=True)
    unique_key = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    variables = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=50, default="Pending", db_index=True)
    tracking_token = models.CharField(max_length=64, unique=True, db_index=True)
    error_log = models.TextField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    opened_at = models.DateTimeField(blank=True, null=True)
    clicked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["store", "status"]),
            models.Index(fields=["store", "created_at"]),
            models.Index(fields=["automation", "status"]),
            models.Index(fields=["tracking_token"]),
            models.Index(fields=["unique_key"]),
        ]

    def save(self, *args, **kwargs):
        if not self.tracking_token:
            self.tracking_token = uuid.uuid4().hex
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.recipient_email} - {self.status} ({self.created_at})"
