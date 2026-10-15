from django.db import models
from Accounts.models import Store
from django.conf import settings

class ShopifySettings(models.Model):
    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="shopify_settings")
    shop_url = models.CharField(max_length=500, blank=True, null=True)
    shopify_access_token = models.CharField(max_length=255, blank=True, null=True)
    custom_api_key = models.CharField(max_length=255, blank=True, null=True)
    custom_api_secret = models.CharField(max_length=255, blank=True, null=True)
    shopify_refresh_token = models.CharField(max_length=255, blank=True, null=True)
    shopify_token_expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_api_key(self):
        if self.custom_api_key and self.custom_api_key.strip():
            return self.custom_api_key.strip()
        return getattr(settings, "SHOPIFY_API_KEY", "")

    def get_api_secret(self):
        if self.custom_api_secret and self.custom_api_secret.strip():
            return self.custom_api_secret.strip()
        return getattr(settings, "SHOPIFY_API_SECRET", "")

    def __str__(self):
        return f"{self.store.name} - {self.shop_url}"

DEFAULT_VERIFIER_DOMAINS = [
    "aim.com", "aol.co.uk", "aol.com", "att.net", "bell.net", "btinternet.com", "charter.net",
    "comcast.net", "cox.net", "email.com", "fastmail.com", "fastmail.fm", "free.fr", "freenet.de",
    "gmail.com", "gmx.com", "gmx.de", "gmx.net", "googlemail.com", "hotmail.co.uk", "hotmail.com",
    "hotmail.de", "hotmail.es", "hotmail.fr", "hotmail.it", "icloud.com", "laposte.net", "live.co.uk",
    "live.com", "live.fr", "mac.com", "mail.com", "me.com", "msn.com", "orange.fr", "outlook.co.uk",
    "outlook.com", "outlook.de", "outlook.es", "outlook.fr", "passport.com", "pm.me", "proton.me",
    "protonmail.com", "rocketmail.com", "rogers.com", "sbcglobal.net", "sfr.fr", "shaw.ca", "sky.com",
    "t-online.de", "usa.com", "verizon.net", "virginmedia.com", "web.de", "yahoo.ca", "yahoo.co.uk",
    "yahoo.com", "yahoo.com.au", "yahoo.de", "yahoo.es", "yahoo.fr", "yahoo.in", "yahoo.it",
    "yandex.by", "yandex.com", "yandex.kz", "yandex.ru", "ymail.com", "zoho.com", "zohomail.com"
]


class EmailVerifier(models.Model):
    """
    Stores whitelist of allowed/verified email domains per store.
    """
    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="email_verifier")
    domains = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = "Active" if self.is_active else "Disabled"
        return f"EmailVerifier ({len(self.domains or [])} domains, {status})"

