import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from Accounts.models import Store


class EmailProvider:
    def __init__(self, store: Store):
        self.store = store

    def send(self, to_email, subject, html_body, from_name=None):
        from_name = from_name or self.store.default_from_name or self.store.name
        from_email = self.store.default_from_email

        if not from_email:
            # Fallback to the client's admin/first user email
            from Accounts.models import ClientUser
            client_user = ClientUser.objects.filter(client=self.store.client).first()
            if client_user:
                from_email = client_user.user.email
            else:
                from_email = "noreply@mailflow.com"

        if self.store.email_provider == "sendgrid":
            if not self.store.sendgrid_api_key:
                if self.store.smtp_host:
                    return self._send_smtp(to_email, subject, html_body, from_email, from_name)
                else:
                    return self._send_smtp_mock(to_email, subject, html_body, from_email, from_name)
            return self._send_sendgrid(to_email, subject, html_body, from_email, from_name)

        if not self.store.smtp_host:
            return self._send_smtp_mock(to_email, subject, html_body, from_email, from_name)
        return self._send_smtp(to_email, subject, html_body, from_email, from_name)

    def _send_smtp_mock(self, to_email, subject, html_body, from_email, from_name):
        import os
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(backend_dir, "sent_emails.log")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("="*80 + "\n")
                f.write(f"FROM: {from_name} <{from_email}>\n")
                f.write(f"TO: {to_email}\n")
                f.write(f"SUBJECT: {subject}\n")
                f.write("BODY:\n")
                f.write(html_body + "\n")
                f.write("="*80 + "\n\n")
        except Exception as e:
            print(f"Error writing mock email log: {e}")
        return 200

    def _send_sendgrid(self, to_email, subject, html_body, from_email, from_name):
        try:
            message = Mail(
                from_email=(from_email, from_name),
                to_emails=to_email,
                subject=subject,
                html_content=html_body,
            )
            client = SendGridAPIClient(self.store.sendgrid_api_key)
            response = client.send(message)
            return response.status_code
        except Exception as e:
            print(f"SendGrid sending failed: {e}. Falling back to mock email logging.")
            return self._send_smtp_mock(to_email, subject, html_body, from_email, from_name)

    def _send_smtp(self, to_email, subject, html_body, from_email, from_name):
        try:
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
        except Exception as e:
            print(f"SMTP sending failed: {e}.")
            raise e
