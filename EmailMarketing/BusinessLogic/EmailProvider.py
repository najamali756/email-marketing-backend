import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from Stores.models import Store


class EmailProvider:
    def __init__(self, store: Store):
        self.store = store

    def send(self, to_email, subject, html_body, from_name=None):
        from_name = from_name or self.store.default_from_name or self.store.name
        from_email = self.store.default_from_email

        if not from_email:
            raise ValueError("Store default_from_email is not configured.")

        if self.store.email_provider == "sendgrid":
            return self._send_sendgrid(to_email, subject, html_body, from_email, from_name)
        return self._send_smtp(to_email, subject, html_body, from_email, from_name)

    def _send_sendgrid(self, to_email, subject, html_body, from_email, from_name):
        if not self.store.sendgrid_api_key:
            raise ValueError("SendGrid API key is not configured for this store.")

        message = Mail(
            from_email=(from_email, from_name),
            to_emails=to_email,
            subject=subject,
            html_content=html_body,
        )
        client = SendGridAPIClient(self.store.sendgrid_api_key)
        response = client.send(message)
        return response.status_code

    def _send_smtp(self, to_email, subject, html_body, from_email, from_name):
        if not self.store.smtp_host:
            raise ValueError("SMTP host is not configured for this store.")

        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = f"{from_name} <{from_email}>"
        message["To"] = to_email
        message.attach(MIMEText(html_body, "html"))

        if self.store.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.store.smtp_host, self.store.smtp_port)
        else:
            server = smtplib.SMTP(self.store.smtp_host, self.store.smtp_port)
            if self.store.smtp_use_tls:
                server.starttls(context=ssl.create_default_context())

        if self.store.smtp_username and self.store.smtp_password:
            server.login(self.store.smtp_username, self.store.smtp_password)

        server.sendmail(from_email, [to_email], message.as_string())
        server.quit()
        return 200
