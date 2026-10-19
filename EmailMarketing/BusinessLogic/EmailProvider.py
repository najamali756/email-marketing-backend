import logging
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from django.conf import settings
from django.utils.text import slugify
from Accounts.models import Store
from EmailMarketing.models import StoreSenderIdentity

logger = logging.getLogger(__name__)


class SMTPSession:
    """
    Maintains a persistent SMTP connection for sending up to max_messages (default 500)
    over a single connection before reconnecting. Automatically reconnects if connection drops.
    """

    def __init__(self, provider, max_messages=500):
        self.provider = provider
        self.max_messages = max_messages
        self.server = None
        self.current_port = None
        self.sent_count = 0

    def connect(self):
        self.close()

        host = getattr(settings, "EMAIL_HOST", "email-smtp.eu-north-1.amazonaws.com")
        port = int(getattr(settings, "EMAIL_PORT", 465))
        use_ssl = getattr(settings, "EMAIL_USE_SSL", True if port == 465 else False)
        use_tls = getattr(settings, "EMAIL_USE_TLS", True if port != 465 else False)
        username = getattr(settings, "EMAIL_HOST_USER", "apikey")
        password = getattr(settings, "EMAIL_HOST_PASSWORD", "")

        connection_attempts = [
            (port, use_ssl, use_tls),
        ]
        if port != 465:
            connection_attempts.append((465, True, False))
        if port != 2587:
            connection_attempts.append((2587, False, True))

        last_conn_exc = None
        server = None
        active_port = port
        for try_port, try_ssl, try_tls in connection_attempts:
            try:
                server = self.provider._connect_smtp(host, try_port, try_ssl, try_tls)
                active_port = try_port
                break
            except Exception as conn_err:
                logger.warning(
                    f"[EMAIL PROVIDER] Connection failed on {host}:{try_port}: {conn_err}. Trying fallback port..."
                )
                last_conn_exc = conn_err

        if server is None:
            err_msg = f"Email delivery connection failed to {host}: {last_conn_exc}"
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg}")
            raise ValueError(err_msg)

        try:
            if username and password:
                server.login(username, password)
            self.server = server
            self.current_port = active_port
            self.sent_count = 0
            return self.server
        except smtplib.SMTPAuthenticationError as e:
            try:
                server.quit()
            except Exception:
                pass
            err_msg = f"SMTP Authentication Error (535): Invalid username or password on {host}:{active_port}."
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg} Details: {e}")
            raise ValueError(err_msg)
        except Exception as e:
            try:
                server.quit()
            except Exception:
                pass
            err_msg = f"Email delivery connection failed to {host}: {str(e)}"
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg}")
            raise ValueError(err_msg)

    def _execute_send(self, from_email, to_email, message_bytes):
        host = getattr(settings, "EMAIL_HOST", "email-smtp.eu-north-1.amazonaws.com")
        try:
            self.server.sendmail(from_email, [to_email], message_bytes)
            self.sent_count += 1
            return 200
        except (
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPConnectError,
            ConnectionResetError,
            BrokenPipeError,
        ) as drop_err:
            logger.warning(
                f"[EMAIL PROVIDER] SMTP disconnected ({drop_err}), reconnecting and retrying once..."
            )
            self.connect()
            self.server.sendmail(from_email, [to_email], message_bytes)
            self.sent_count += 1
            return 200
        except smtplib.SMTPSenderRefused as e:
            err_msg = (
                f"SMTP Sender Refused ({e.smtp_code}): Sender address '{from_email}' rejected. "
                f"Please verify custom sender domain authentication in SendGrid/DNS."
            )
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg} Details: {e}")
            raise ValueError(err_msg)
        except smtplib.SMTPRecipientsRefused as e:
            err_msg = f"SMTP Recipient Refused: Target email address '{to_email}' rejected by server."
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg} Details: {e}")
            raise ValueError(err_msg)
        except smtplib.SMTPDataError as e:
            error_details = (
                e.smtp_error.decode()
                if isinstance(e.smtp_error, bytes)
                else str(e.smtp_error)
            )
            err_msg = f"SMTP Server Rejected Message: {error_details}"
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg}")
            raise ValueError(err_msg)
        except Exception as e:
            err_msg = f"Email delivery failed: {str(e)}"
            logger.error(f"[EMAIL PROVIDER ERROR] {err_msg}")
            raise ValueError(err_msg)

    def send(self, to_email, subject, html_body, from_name=None, unsubscribe_url=None):
        if self.server is None or self.sent_count >= self.max_messages:
            self.connect()

        message, from_email = self.provider.build_mime_message(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            from_name=from_name,
            unsubscribe_url=unsubscribe_url,
        )

        return self._execute_send(from_email, to_email, message.as_bytes())

    def close(self):
        if self.server:
            try:
                self.server.quit()
            except Exception:
                pass
            self.server = None
        self.sent_count = 0

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class EmailProvider:
    def __init__(self, store: Store):
        self.store = store
        self._identity = None
        self._identity_loaded = False

    def _get_identity(self):
        if not self._identity_loaded:
            try:
                self._identity = StoreSenderIdentity.objects.filter(
                    store=self.store, is_active=True
                ).first()
            except Exception:
                self._identity = None
            self._identity_loaded = True
        return self._identity

    def _connect_smtp(self, host, port, use_ssl, use_tls):
        """Helper to establish SMTP connection with SSL/TLS."""
        if port == 465 or use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if use_tls:
                server.starttls(context=ssl.create_default_context())
        return server

    def build_mime_message(
        self, to_email, subject, html_body, from_name=None, unsubscribe_url=None
    ):
        identity = self._get_identity()
        store_name = (
            getattr(self.store, "name", "Store") if self.store else "Store"
        )
        default_from_name = (
            getattr(self.store, "default_from_name", None) if self.store else None
        )

        from_name = from_name or (
            identity.brand_name
            if identity and identity.brand_name
            else default_from_name or store_name
        )

        platform_domain = getattr(
            settings, "SENDGRID_PLATFORM_DOMAIN", "technogroves.com"
        )
        if identity:
            if identity.mode == "platform_domain":
                brand_slug = slugify(identity.brand_name or store_name)
                from_email = f"{brand_slug}@{platform_domain}"
            else:
                if identity.from_email and "@" in identity.from_email:
                    from_email = identity.from_email
                elif identity.domain:
                    from_email = f"info@{identity.domain}"
                else:
                    from_email = (
                        getattr(self.store, "default_from_email", None)
                        if self.store
                        else None
                    ) or f"noreply@{platform_domain}"
            reply_to = identity.reply_to_email or None
        else:
            from_email = (
                getattr(self.store, "default_from_email", None)
                if self.store
                else None
            ) or f"noreply@{platform_domain}"
            reply_to = None

        if not from_email or "@" not in from_email:
            from_email = f"noreply@{platform_domain}"

        message = MIMEMultipart("alternative")
        message["Subject"] = Header(subject or "No Subject", "utf-8")

        if from_name:
            message["From"] = formataddr(
                (str(Header(from_name, "utf-8")), from_email)
            )
        else:
            message["From"] = from_email

        message["To"] = to_email
        if reply_to:
            message["Reply-To"] = reply_to

        if unsubscribe_url:
            message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
            message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        message.attach(MIMEText(html_body or "", "html", "utf-8"))
        return message, from_email

    def get_smtp_session(self, max_messages=500):
        """Returns an SMTPSession for persistent, high-throughput bulk sending."""
        return SMTPSession(self, max_messages=max_messages)

    def send(self, to_email, subject, html_body, from_name=None, unsubscribe_url=None):
        """Single send method - 100% backward compatible with existing callers."""
        session = self.get_smtp_session(max_messages=1)
        try:
            return session.send(
                to_email=to_email,
                subject=subject,
                html_body=html_body,
                from_name=from_name,
                unsubscribe_url=unsubscribe_url,
            )
        finally:
            session.close()
