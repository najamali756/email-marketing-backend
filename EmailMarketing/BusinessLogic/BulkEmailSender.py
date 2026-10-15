import os
import re
import time
import logging

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from Accounts.models import Contact
from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver
from EmailMarketing.BusinessLogic.EmailProvider import EmailProvider
from EmailMarketing.BusinessLogic.TemplateRenderer import TemplateRenderer
from EmailMarketing.BusinessLogic.threading_utils import spawn_thread
from EmailMarketing.models import (
    EmailBrandSettings,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailCampaignStatusEnum,
    EmailRecipientStatusEnum,
    EmailSegment,
)
from shopify_integration.models import EmailVerifier, DEFAULT_VERIFIER_DOMAINS

logger = logging.getLogger(__name__)


def _extract_store_id(store):
    """Safely resolves store ID integer from Store object, Campaign object, dict, int, or str."""
    if store is None:
        return None
    if isinstance(store, int):
        return store
    if isinstance(store, str):
        try:
            return int(store)
        except (ValueError, TypeError):
            return None
    if hasattr(store, "store_id") and store.store_id:
        return store.store_id
    if hasattr(store, "store") and store.store:
        return getattr(store.store, "id", None)
    return getattr(store, "id", None)


def get_allowed_email_domains(store=None):
    """
    Fetches allowed domains directly from DB (no cache).
    Supports per-store verifier or fallback to DEFAULT_VERIFIER_DOMAINS.
    """
    store_id = _extract_store_id(store)
    if not store_id:
        return set(DEFAULT_VERIFIER_DOMAINS)

    try:
        verifier = EmailVerifier.objects.filter(store_id=store_id).first()
        if verifier and verifier.domains:
            return set(verifier.domains)
    except Exception as exc:
        logger.warning(f"[EMAIL VERIFIER] Failed fetching domains for store {store_id}: {exc}")

    return set(DEFAULT_VERIFIER_DOMAINS)


def is_domain_verification_active(store=None):
    """
    Checks if domain whitelist verification is enabled for the specific store.
    Direct DB query, ZERO cache to ensure instant reflection on toggle.
    Defaults to True. If False, domain filtering is bypassed for this store/brand.
    """
    store_id = _extract_store_id(store)
    if not store_id:
        return True

    try:
        verifier = EmailVerifier.objects.filter(store_id=store_id).first()
        if verifier is not None:
            return bool(getattr(verifier, "is_active", True))
    except Exception as exc:
        logger.warning(f"[EMAIL VERIFIER] Failed checking is_active for store {store_id}: {exc}")

    return True


def is_valid_provider_email(email_str, store=None):
    """
    Validates that an email belongs to an allowed provider domain (or provider subdomain).
    Excludes unrecognized/arbitrary domains using EmailVerifier whitelist when active.
    If is_active is False for this store/brand, validation is bypassed and email is allowed.
    """
    if not email_str or "@" not in email_str:
        return False
    parts = str(email_str).strip().lower().split("@")
    if len(parts) != 2:
        return False
    domain = parts[1].strip()
    if not domain or "." not in domain:
        return False

    # Check if verification is active for this brand/store (defaults to True)
    if not is_domain_verification_active(store):
        return True

    allowed_domains = get_allowed_email_domains(store)

    if domain in allowed_domains:
        return True

    for pop in allowed_domains:
        if domain.endswith("." + pop):
            return True

    return False


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

    def build_recipients(self, segment_ids=None, target_all_contacts=False, specific_emails=None):

        campaign = self._load_campaign()
        if not campaign:
            raise ValueError("Campaign not found")

        contacts = Contact.objects.none()

        if specific_emails:
            contacts = Contact.objects.filter(store=campaign.store, email__in=specific_emails)
        elif target_all_contacts:
            contacts = Contact.objects.filter(store=campaign.store )#accept_email_marketing=True
        elif segment_ids:
            segments = EmailSegment.objects.filter(id__in=segment_ids, store=campaign.store)
            for seg in segments:
                q = AudienceResolver(campaign.store).resolve(seg.filter_config or {})
                contacts = contacts | q
            contacts = contacts.distinct()
        elif campaign.segment:
            filter_config = campaign.segment.filter_config or {}
            contacts = AudienceResolver(campaign.store).resolve(filter_config)
        else:
            contacts = Contact.objects.filter(store=campaign.store, accept_email_marketing=True)

        existing_contact_ids = set(
            EmailCampaignRecipient.objects.filter(campaign=campaign).values_list("contact_id", flat=True)
        )


        recipients = []
        for contact in contacts.iterator(chunk_size=500):
            if contact.id in existing_contact_ids or not contact.email:
                continue
            email_clean = contact.email.strip().lower()
            if not is_valid_provider_email(email_clean, store=campaign.store):
                continue
            recipients.append(
                EmailCampaignRecipient(
                    campaign=campaign,
                    contact=contact,
                    email=email_clean,
                    status=EmailRecipientStatusEnum.pending.value,
                    personalization={
                        "first_name": contact.first_name or "",
                        "last_name": contact.last_name or "",
                        "city": contact.city or "",
                        "country": contact.country or "",
                        "total_orders": str(contact.total_orders or 0),
                        "total_spent": str(contact.total_spent or 0),
                        "external_id": str(contact.external_id or ""),
                    }
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

            campaign.refresh_from_db()
            if campaign.status in [EmailCampaignStatusEnum.cancelled.value, "Paused", "Cancelled"]:
                print(f"[BULK EMAIL SENDER] Campaign {campaign.id} status is '{campaign.status}'. Stopping batch send loop.")
                return

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
                raw_preview_text = campaign.preview_text or (campaign.template.preview_text if campaign.template else "")
                rendered_preview = TemplateRenderer.render(raw_preview_text, context)
                if rendered_preview:
                    body = TemplateRenderer.inject_preview_text(body, rendered_preview)
                body = self._instrument_html_body(body, recipient)
                subject = TemplateRenderer.render(campaign.subject, context)
                from_name = campaign.from_name or (brand_settings.default_from_name if brand_settings else None)

                try:
                    provider.send(
                        to_email=recipient.email,
                        subject=subject,
                        html_body=body,
                        from_name=from_name,
                        unsubscribe_url=self._build_unsubscribe_url(recipient),
                    )
                    recipient.status = EmailRecipientStatusEnum.sent.value
                    recipient.sent_at = timezone.now()
                    recipient.error_message = None
                except Exception as exc:
                    recipient.status = EmailRecipientStatusEnum.failed.value
                    recipient.error_message = str(exc)

                recipient.save(update_fields=["status", "sent_at", "error_message", "updated_at"])

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

    def _instrument_html_body(self, html_content, recipient):
        if not html_content:
            return ""

        unsub_url = self._build_unsubscribe_url(recipient)
        unsub_tag = f'<a href="{unsub_url}" style="color: #4f46e5; text-decoration: underline;">Unsubscribe</a>'

        html_content = re.sub(
            r'\[([^\]]*)\]\((https?://[^\s\)]+emailMarketing/unsubscribe[^\s\)]*)\)',
            r'<a href="\g<2>" style="color: #4f46e5; text-decoration: underline;">Unsubscribe</a>',
            html_content,
            flags=re.IGNORECASE
        )

        html_content = re.sub(
            r"""<a([^>]*?href=["'][^"']*emailMarketing/unsubscribe[^"']*["'][^>]*?)>\s*https?://[^\s<]+\s*</a>""",
            rf'<a style="color: #4f46e5; text-decoration: underline;">Unsubscribe</a>',
            html_content,
            flags=re.IGNORECASE
        )

        html_content = html_content.replace('href="{{ unsubscribe_url }}"', f'href="{unsub_url}"')
        html_content = html_content.replace('href="{unsubscribe_url}"', f'href="{unsub_url}"')
        html_content = html_content.replace("{{ unsubscribe_url }}", unsub_tag).replace("{unsubscribe_url}", unsub_tag)
        html_content = html_content.replace("{{ unsubscribe_link }}", unsub_tag).replace("{unsubscribe_link}", unsub_tag)

        html_content = re.sub(
            r"""(?<!href=["'])(https?://[^\s"'<]+emailMarketing/unsubscribe[^\s"'<]*)""",
            unsub_tag,
            html_content,
            flags=re.IGNORECASE
        )

   
        exec_id = str(recipient.tracking_token)

        def _append_execution_param(match):
            url = match.group(1)
            if url.startswith("#") or url.startswith("mailto:") or url.startswith("tel:") or "unsubscribe" in url.lower():
                return f'href="{url}"'
            sep = "&" if "?" in url else "?"
            tagged_url = f"{url}{sep}execution_id={exec_id}"
            return f'href="{tagged_url}"'

        html_content = re.sub(r'href=["\']([^"\']+)["\']', _append_execution_param, html_content)

        if self.public_url:
            open_pixel_url = f"{self.public_url.rstrip('/')}/emailMarketing/track/open?token={recipient.tracking_token}"
            pixel_tag = f'<img src="{open_pixel_url}" alt="" width="1" height="1" border="0" style="display:none;width:1px;height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;" />'
            if "</body>" in html_content:
                html_content = html_content.replace("</body>", f"{pixel_tag}</body>")
            else:
                html_content += pixel_tag

        return html_content

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
        html_body = TemplateRenderer.render(html_template, context)
        raw_preview_text = campaign.preview_text or (campaign.template.preview_text if campaign.template else "")
        rendered_preview = TemplateRenderer.render(raw_preview_text, context)
        if rendered_preview:
            html_body = TemplateRenderer.inject_preview_text(html_body, rendered_preview)

        provider.send(
            to_email=to_email,
            subject=TemplateRenderer.render(campaign.subject, context),
            html_body=html_body,
            from_name=campaign.from_name,
        )
