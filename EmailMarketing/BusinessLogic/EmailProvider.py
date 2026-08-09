import smtplib
import ssl
import logging
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from django.conf import settings
from django.utils.text import slugify
from EmailMarketing.models import StoreSenderIdentity
from Accounts.models import Store

logger = logging.getLogger(__name__)


class EmailProvider:
    def __init__(self, store: Store):
        self.store = store

    def send(self, to_email, subject, html_body, from_name=None, unsubscribe_url=None):
        # 1. Resolve from_email and reply_to based on StoreSenderIdentity
        identity = StoreSenderIdentity.objects.filter(store=self.store, is_active=True).first()
        
        from_name = from_name or (identity.brand_name if identity and identity.brand_name else self.store.default_from_name or self.store.name)
        
        # Determine from_email depending on the mode
        platform_domain = getattr(settings, "SENDGRID_PLATFORM_DOMAIN", "ntechgreenbridge.com")
        if identity:
            if identity.mode == "platform_domain":
                brand_slug = slugify(identity.brand_name or self.store.name)
                from_email = f"{brand_slug}@{platform_domain}"
            else:  # custom_domain
                if identity.from_email and "@" in identity.from_email:
                    from_email = identity.from_email
                elif identity.domain:
                    from_email = f"info@{identity.domain}"
                else:
                    from_email = self.store.default_from_email or f"noreply@{platform_domain}"
            reply_to = identity.reply_to_email or None
        else:
            # Fall back to default_from_email or platform domain
            from_email = self.store.default_from_email or f"noreply@{platform_domain}"
            reply_to = None

        # Sanity check on from_email
        if not from_email or "@" not in from_email:
            from_email = f"noreply@{platform_domain}"

        # 2. Get SMTP settings
        host = getattr(settings, "EMAIL_HOST", "smtp.sendgrid.net")
        port = int(getattr(settings, "EMAIL_PORT", 587))
        username = getattr(settings, "EMAIL_HOST_USER", "apikey")
        password = getattr(settings, "EMAIL_HOST_PASSWORD", "")

        # 3. Build MIMEMultipart email message with full UTF-8 Header support
        message = MIMEMultipart("alternative")
        message["Subject"] = Header(subject or "No Subject", "utf-8")
        
        if from_name:
            message["From"] = formataddr((str(Header(from_name, "utf-8")), from_email))
        else:
            message["From"] = from_email

        message["To"] = to_email
        if reply_to:
            message["Reply-To"] = reply_to

        # Attach RFC 8058 1-Click Unsubscribe headers for Gmail / Yahoo / Outlook
        if unsubscribe_url:
            message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
            message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        message.attach(MIMEText(html_body or "", "html", "utf-8"))

        # 4. Connect and send email via SMTP server
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                server = smtplib.SMTP(host, port, timeout=15)
                if getattr(settings, "EMAIL_USE_TLS", True):
                    server.starttls(context=ssl.create_default_context())

            if username and password:
                logger.info(f"[EMAIL PROVIDER] Connecting to {host}:{port} with user: {username}")
                logger.info(f"[EMAIL PROVIDER] Sending from '{from_name}' <{from_email}> to <{to_email}>")
                server.login(username, password)

            server.sendmail(from_email, [to_email], message.as_bytes())
            server.quit()
            return 200
        except smtplib.SMTPAuthenticationError as e:
            err_msg = f"SMTP Authentication Error (535): Invalid username or password on {host}:{port}."
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg} Details: {e}")
            raise ValueError(err_msg)
        except smtplib.SMTPSenderRefused as e:
            err_msg = f"SMTP Sender Refused ({e.smtp_code}): Sender address '{from_email}' rejected. Please verify custom sender domain authentication in SendGrid/DNS."
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg} Details: {e}")
            raise ValueError(err_msg)
        except smtplib.SMTPRecipientsRefused as e:
            err_msg = f"SMTP Recipient Refused: Target email address '{to_email}' rejected by server."
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg} Details: {e}")
            raise ValueError(err_msg)
        except smtplib.SMTPDataError as e:
            error_details = e.smtp_error.decode() if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
            err_msg = f"SMTP Server Rejected Message: {error_details}"
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg}")
            raise ValueError(err_msg)
        except Exception as e:
            err_msg = f"Email delivery failed: {str(e)}"
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg}")
            raise ValueError(err_msg)
