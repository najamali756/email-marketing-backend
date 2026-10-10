import logging
import re
import threading
import requests
from django.conf import settings
from django.utils import timezone

from EmailMarketing.models import EmailTemplate
from EmailMarketing.BusinessLogic.EmailProvider import EmailProvider
from automation.models import NotificationSend

logger = logging.getLogger(__name__)


def replace_variables(text: str, variables: dict) -> str:
    """Replaces {{ variable_name }} with corresponding value from variables dictionary."""
    if not text or not variables:
        return text or ""

    def _replace(match):
        var_name = match.group(1).strip()
        val = variables.get(var_name)
        if val is not None:
            return str(val)
        return match.group(0)

    return re.sub(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}", _replace, text)


def notify_external_webhook(callback_url: str, payload: dict):
    """
    Sends an asynchronous HTTP POST status notification to the client's external webhook.
    """
    if not callback_url:
        return

    def _post():
        try:
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Omnexa-Automation-Webhook/1.0",
            }
            resp = requests.post(callback_url, json=payload, headers=headers, timeout=10)
            logger.info(f"Notified webhook {callback_url}: status={resp.status_code}")
        except Exception as exc:
            logger.warning(f"Failed to notify webhook {callback_url}: {exc}")

    thread = threading.Thread(target=_post, daemon=True)
    thread.start()


def dispatch_automation_email(notification_send_id: int):
    """
    Dispatches the automation email in a separate non-blocking daemon thread.
    Handles HTML rendering, open tracking beacon injection, EmailProvider delivery,
    and webhook callback notification.
    """
    def _worker():
        try:
            send_record = (
                NotificationSend.objects.select_related("automation", "store")
                .filter(id=notification_send_id)
                .first()
            )
            if not send_record:
                logger.error(f"NotificationSend #{notification_send_id} not found for dispatch")
                return

            automation = send_record.automation
            store = send_record.store
            variables = send_record.variables or {}

            # 1. Subject & Preview Text
            subject_raw = (
                (automation.subject if automation else None)
                or (automation.name if automation else "Notification")
            )
            subject = replace_variables(subject_raw, variables)

            # 2. Template Resolution
            raw_html = None
            if automation and automation.template_name:
                tmpl = EmailTemplate.objects.filter(
                    store=store, name=automation.template_name
                ).first()
                if tmpl and tmpl.html_content:
                    raw_html = tmpl.html_content

            if not raw_html:
                # Clean transactional fallback HTML
                raw_html = f"""
                <!DOCTYPE html>
                <html>
                <head><meta charset='utf-8'></head>
                <body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:#f8fafc;padding:32px;color:#0f172a;'>
                  <div style='max-width:580px;margin:0 auto;background:#ffffff;border-radius:12px;padding:32px;border:1px solid #e2e8f0;box-shadow:0 4px 16px rgba(0,0,0,0.06);'>
                    <h2 style='margin-top:0;color:#0f172a;'>{subject}</h2>
                    <p style='color:#475569;font-size:15px;line-height:1.6;'>Hello,</p>
                    <p style='color:#475569;font-size:14px;line-height:1.6;'>You have a new update regarding your order/request.</p>
                    <div style='margin:24px 0;padding:16px;background:#f1f5f9;border-radius:8px;'>
                      <strong>Reference ID:</strong> {send_record.unique_key or '—'}<br/>
                    </div>
                    <p style='font-size:12px;color:#94a3b8;margin-top:32px;border-top:1px solid #e2e8f0;padding-top:16px;'>Sent via Omnexa Automations.</p>
                  </div>
                </body>
                </html>
                """

            # 3. Variable Replacement in HTML
            rendered_html = replace_variables(raw_html, variables)

            # 4. Inject 1x1 Open Tracking Beacon
            pixel_url = f"https://marketing-be.technogroves.com/automations/track/open?token={send_record.tracking_token}"
            pixel_tag = (
                f'<img src="{pixel_url}" alt="" width="1" height="1" border="0" '
                f'style="display:block;width:1px!important;min-width:1px!important;height:1px!important;min-height:1px!important;border:0!important;margin:0!important;padding:0!important;outline:none!important;" />'
            )

            if "</body>" in rendered_html:
                rendered_html = rendered_html.replace("</body>", f"{pixel_tag}</body>")
            else:
                rendered_html += pixel_tag

            # 5. Send via Store Email Provider
            provider = EmailProvider(store)
            provider.send(
                to_email=send_record.recipient_email,
                subject=subject,
                html_body=rendered_html,
                from_name=store.default_from_name or store.name,
            )

            # 6. Mark Record as Sent
            send_record.status = "Sent"
            send_record.sent_at = timezone.now()
            send_record.error_log = None
            send_record.save(update_fields=["status", "sent_at", "error_log", "updated_at"])

            # 7. Notify External Callback Webhook if enabled on this workflow
            if automation and getattr(automation, "send_callback", False) and store and store.callback_url:
                cb_token = getattr(store, "third_party_token", None) or getattr(store, "api_key", None)
                notify_external_webhook(
                    store.callback_url,
                    {
                        "event": "email_dispatched",
                        "unique_key": send_record.unique_key,
                        "notification_name": (automation.external_name or automation.name) if automation else "Notification",
                        "recipient_email": send_record.recipient_email,
                        "status": "Sent",
                        "timestamp": send_record.sent_at.isoformat(),
                        "token": cb_token,
                    },
                )

        except Exception as exc:
            logger.exception(f"Automation email dispatch failed for record #{notification_send_id}: {exc}")
            try:
                rec = NotificationSend.objects.select_related("automation", "store").filter(id=notification_send_id).first()
                if rec:
                    rec.status = "Failed"
                    rec.error_log = str(exc)
                    rec.save(update_fields=["status", "error_log", "updated_at"])

                    if rec.automation and getattr(rec.automation, "send_callback", False) and rec.store and rec.store.callback_url:
                        cb_token = getattr(rec.store, "third_party_token", None) or getattr(rec.store, "api_key", None)
                        notify_external_webhook(
                            rec.store.callback_url,
                            {
                                "event": "email_failed",
                                "unique_key": rec.unique_key,
                                "notification_name": (rec.automation.external_name or rec.automation.name) if rec.automation else "Notification",
                                "recipient_email": rec.recipient_email,
                                "status": "Failed",
                                "error": str(exc),
                                "timestamp": timezone.now().isoformat(),
                                "token": cb_token,
                            },
                        )
            except Exception:
                pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
