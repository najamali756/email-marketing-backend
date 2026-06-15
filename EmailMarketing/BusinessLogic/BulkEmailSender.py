import os
import time

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from EmailMarketing.BusinessLogic.EmailProvider import EmailProvider
from EmailMarketing.BusinessLogic.TemplateRenderer import TemplateRenderer
from EmailMarketing.BusinessLogic.threading_utils import spawn_thread
from EmailMarketing.models import (
    EmailBrandSettings,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailCampaignStatusEnum,
    EmailRecipientStatusEnum,
)


class BulkEmailSender:
    def __init__(self, campaign_id):
        self.campaign_id = campaign_id
        self.batch_size = getattr(settings, "EMAIL_MARKETING_BATCH_SIZE", 100)
        self.batch_sleep = getattr(settings, "EMAIL_MARKETING_BATCH_SLEEP", 0.5)
        self.public_url = getattr(settings, "EMAIL_MARKETING_PUBLIC_URL", "")

    def _load_campaign(self):
        return (
            EmailCampaign.objects.select_related("store", "template", "segment")
            .filter(id=self.campaign_id)
            .first()
        )

    def build_recipients(self):
        from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver

        campaign = self._load_campaign()
        if not campaign:
            raise ValueError("Campaign not found")

        filter_config = campaign.segment.filter_config if campaign.segment else {}
        contacts = AudienceResolver(campaign.store).resolve(filter_config)

        existing_contact_ids = set(
            EmailCampaignRecipient.objects.filter(campaign=campaign).values_list("contact_id", flat=True)
        )

        recipients = []
        for contact in contacts.iterator(chunk_size=500):
            if contact.id in existing_contact_ids or not contact.email:
                continue
            recipients.append(
                EmailCampaignRecipient(
                    campaign=campaign,
                    contact=contact,
                    email=contact.email,
                    status=EmailRecipientStatusEnum.pending.value,
                )
            )

        if recipients:
            EmailCampaignRecipient.objects.bulk_create(recipients, batch_size=500)

        total = EmailCampaignRecipient.objects.filter(campaign=campaign).count()
        campaign.total_recipients = total
        campaign.save(update_fields=["total_recipients", "updated_at"])
        return total

    def send_async(self):
        spawn_thread(f"email_campaign_{self.campaign_id}", self.send_in_batches)

    def send_in_batches(self):
        close_old_connections()
        campaign = self._load_campaign()
        if not campaign or campaign.status == EmailCampaignStatusEnum.sent.value:
            return

        campaign.status = EmailCampaignStatusEnum.sending.value
        campaign.last_error = None
        campaign.save(update_fields=["status", "last_error", "updated_at"])

        brand_settings = EmailBrandSettings.objects.filter(store=campaign.store).first()
        provider = EmailProvider(campaign.store)
        html_template = campaign.html_content or (campaign.template.html_content if campaign.template else "")

        while True:
            close_old_connections()
            batch = list(
                EmailCampaignRecipient.objects.filter(
                    campaign=campaign,
                    status=EmailRecipientStatusEnum.pending.value,
                )
                .select_related("contact")
                .order_by("id")[: self.batch_size]
            )
            if not batch:
                break

            for recipient in batch:
                recipient.status = EmailRecipientStatusEnum.in_process.value
                recipient.save(update_fields=["status", "updated_at"])

                context = TemplateRenderer.build_contact_context(
                    recipient.contact,
                    brand_settings=brand_settings,
                    extra={
                        "unsubscribe_url": self._build_unsubscribe_url(recipient),
                        **(recipient.personalization or {}),
                    },
                )
                body = TemplateRenderer.render(html_template, context)
                subject = TemplateRenderer.render(campaign.subject, context)
                from_name = campaign.from_name or (brand_settings.default_from_name if brand_settings else None)

                try:
                    provider.send(
                        to_email=recipient.email,
                        subject=subject,
                        html_body=body,
                        from_name=from_name,
                    )
                    recipient.status = EmailRecipientStatusEnum.sent.value
                    recipient.sent_at = timezone.now()
                    recipient.error_message = None
                except Exception as exc:
                    recipient.status = EmailRecipientStatusEnum.failed.value
                    recipient.error_message = str(exc)

                recipient.save(update_fields=["status", "sent_at", "error_message", "updated_at"])

            # Real-time progress update
            campaign.sent_count = EmailCampaignRecipient.objects.filter(
                campaign=campaign, status=EmailRecipientStatusEnum.sent.value
            ).count()
            campaign.failed_count = EmailCampaignRecipient.objects.filter(
                campaign=campaign, status=EmailRecipientStatusEnum.failed.value
            ).count()
            campaign.skipped_count = EmailCampaignRecipient.objects.filter(
                campaign=campaign, status=EmailRecipientStatusEnum.skipped.value
            ).count()
            campaign.save(update_fields=["sent_count", "failed_count", "skipped_count", "updated_at"])

            if self.batch_sleep:
                time.sleep(self.batch_sleep)

        self._finalize_campaign(campaign)

    def _finalize_campaign(self, campaign):
        campaign.sent_count = EmailCampaignRecipient.objects.filter(
            campaign=campaign, status=EmailRecipientStatusEnum.sent.value
        ).count()
        campaign.failed_count = EmailCampaignRecipient.objects.filter(
            campaign=campaign, status=EmailRecipientStatusEnum.failed.value
        ).count()
        campaign.skipped_count = EmailCampaignRecipient.objects.filter(
            campaign=campaign, status=EmailRecipientStatusEnum.skipped.value
        ).count()

        pending = EmailCampaignRecipient.objects.filter(
            campaign=campaign, status=EmailRecipientStatusEnum.pending.value
        ).count()

        if pending == 0:
            campaign.status = (
                EmailCampaignStatusEnum.failed.value
                if campaign.sent_count == 0 and campaign.failed_count > 0
                else EmailCampaignStatusEnum.sent.value
            )
            if campaign.status == EmailCampaignStatusEnum.sent.value:
                campaign.sent_at = timezone.now()
        else:
            campaign.status = EmailCampaignStatusEnum.failed.value
            campaign.last_error = "Campaign finished with pending recipients"

        campaign.save(
            update_fields=[
                "status",
                "sent_count",
                "failed_count",
                "skipped_count",
                "sent_at",
                "last_error",
                "updated_at",
            ]
        )

    def _build_unsubscribe_url(self, recipient):
        if not self.public_url:
            return f"/emailMarketing/unsubscribe?token={recipient.tracking_token}"
        return f"{self.public_url.rstrip('/')}/emailMarketing/unsubscribe?token={recipient.tracking_token}"

    def send_test_email(self, to_email, personalization=None):
        campaign = self._load_campaign()
        if not campaign:
            raise ValueError("Campaign not found")

        brand_settings = EmailBrandSettings.objects.filter(store=campaign.store).first()
        provider = EmailProvider(campaign.store)
        html_template = campaign.html_content or (campaign.template.html_content if campaign.template else "")

        context = {
            "first_name": "Test",
            "last_name": "User",
            "email": to_email,
            "store_name": brand_settings.store_name if brand_settings else campaign.store.name,
            "unsubscribe_url": "#",
            **(personalization or {})
        }
        provider.send(
            to_email=to_email,
            subject=TemplateRenderer.render(campaign.subject, context),
            html_body=TemplateRenderer.render(html_template, context),
            from_name=campaign.from_name,
        )
