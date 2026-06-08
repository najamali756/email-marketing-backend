import uuid
from enum import Enum

from django.db import models

from Stores.models import Contact, Store, TimeStampedModel


class EmailCampaignStatusEnum(Enum):
    draft = "Draft"
    scheduled = "Scheduled"
    sending = "Sending"
    sent = "Sent"
    failed = "Failed"
    cancelled = "Cancelled"


class EmailRecipientStatusEnum(Enum):
    pending = "Pending"
    in_process = "InProcess"
    sent = "Sent"
    failed = "Failed"
    skipped = "Skipped"


class EmailTemplateCategoryEnum(Enum):
    promotional = "Promotional"
    newsletter = "Newsletter"
    product = "Product"
    seasonal = "Seasonal"
    blank = "Blank"


class EmailBrandSettings(TimeStampedModel):
    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="email_brand_settings")
    store_name = models.CharField(max_length=255, blank=True, null=True)
    store_logo_url = models.TextField(blank=True, null=True)
    brand_color = models.CharField(max_length=20, default="#3B82F6")
    default_from_name = models.CharField(max_length=255, blank=True, null=True)
    sender_email = models.EmailField(blank=True, null=True)
    notify_campaign_sent = models.BooleanField(default=True)
    notify_weekly_reports = models.BooleanField(default=True)
    notify_new_subscriber = models.BooleanField(default=False)


class EmailTemplate(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="email_templates")
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=50, default=EmailTemplateCategoryEnum.promotional.value)
    subject = models.CharField(max_length=500, blank=True, null=True)
    preview_text = models.CharField(max_length=500, blank=True, null=True)
    html_content = models.TextField(blank=True, null=True)
    editor_json = models.TextField(blank=True, null=True)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)


class EmailSegment(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="email_segments")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    filter_config = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False)
    cached_contact_count = models.IntegerField(default=0)


class EmailCampaign(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="email_campaigns")
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=500)
    preview_text = models.CharField(max_length=500, blank=True, null=True)
    template = models.ForeignKey(
        EmailTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns"
    )
    segment = models.ForeignKey(
        EmailSegment, on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns"
    )
    status = models.CharField(max_length=50, default=EmailCampaignStatusEnum.draft.value)
    campaign_type = models.CharField(max_length=50, default="Promotional")
    scheduled_at = models.DateTimeField(blank=True, null=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    from_name = models.CharField(max_length=255, blank=True, null=True)
    html_content = models.TextField(blank=True, null=True)
    total_recipients = models.IntegerField(default=0)
    sent_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    skipped_count = models.IntegerField(default=0)
    open_count = models.IntegerField(default=0)
    click_count = models.IntegerField(default=0)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    last_error = models.TextField(blank=True, null=True)


class EmailCampaignRecipient(TimeStampedModel):
    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="email_campaign_recipients")
    email = models.EmailField()
    status = models.CharField(max_length=50, default=EmailRecipientStatusEnum.pending.value)
    tracking_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sent_at = models.DateTimeField(blank=True, null=True)
    opened_at = models.DateTimeField(blank=True, null=True)
    clicked_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    personalization = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("campaign", "contact")
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["tracking_token"]),
        ]


class EmailUnsubscribe(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="email_unsubscribes")
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True)
    email = models.EmailField()

    class Meta:
        unique_together = ("store", "email")
