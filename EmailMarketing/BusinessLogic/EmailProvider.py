import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.conf import settings
from django.utils.text import slugify
from EmailMarketing.models import StoreSenderIdentity
from Accounts.models import Store


class EmailProvider:
    def __init__(self, store: Store):
        self.store = store

    def send(self, to_email, subject, html_body, from_name=None):
        # 1. Resolve from_email and reply_to based on StoreSenderIdentity
        identity = StoreSenderIdentity.objects.filter(store=self.store, is_active=True).first()
        
        from_name = from_name or (identity.brand_name if identity else self.store.default_from_name or self.store.name)
        
        # Determine from_email depending on the mode
        platform_domain = getattr(settings, "SENDGRID_PLATFORM_DOMAIN", "ntechgreenbridge.com")
        if identity and identity.status == "verified":
            if identity.mode == "platform_domain":
                brand_slug = slugify(identity.brand_name)
                from_email = f"{brand_slug}@{platform_domain}"
            else: # custom_domain
                from_email = identity.from_email
            reply_to = identity.reply_to_email
        else:
            # Fall back to default_from_email since it may be verified as a single sender
            from_email = self.store.default_from_email or f"noreply@{platform_domain}"
            reply_to = None

        # 2. Get SMTP settings
        host = getattr(settings, "EMAIL_HOST", "smtp.sendgrid.net")
        port = getattr(settings, "EMAIL_PORT", 587)
        username = getattr(settings, "EMAIL_HOST_USER", "apikey")
        password = getattr(settings, "EMAIL_HOST_PASSWORD", "")

        # 3. Send using SendGrid global SMTP
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{from_name} <{from_email}>"
        message["To"] = to_email
        if reply_to:
            message["Reply-To"] = reply_to
        message.attach(MIMEText(html_body, "html"))

        if port == 465:
            server = smtplib.SMTP_SSL(host, port)
        else:
            server = smtplib.SMTP(host, port)
            if getattr(settings, "EMAIL_USE_TLS", True):
                server.starttls(context=ssl.create_default_context())

        if username and password:
            print(f"[DEBUG EMAIL SENDER] Connecting to SMTP: {host}:{port} with user: {username}, password length: {len(password) if password else 0}")
            print(f"[DEBUG EMAIL SENDER] Resolving From: {from_name} <{from_email}>, To: {to_email}, Reply-To: {reply_to}")
            server.login(username, password)

        server.sendmail(from_email, [to_email], message.as_string())
        server.quit()
        return 200
