from django.db import models
from Accounts.models import Store

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
        from django.conf import settings
        return getattr(settings, "SHOPIFY_API_KEY", "")

    def get_api_secret(self):
        if self.custom_api_secret and self.custom_api_secret.strip():
            return self.custom_api_secret.strip()
        from django.conf import settings
        return getattr(settings, "SHOPIFY_API_SECRET", "")

    def __str__(self):
        return f"{self.store.name} - {self.shop_url}"
