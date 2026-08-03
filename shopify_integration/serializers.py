from rest_framework import serializers
from shopify_integration.models import ShopifySettings

class ShopifySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopifySettings
        fields = ('id', 'shop_url', 'shopify_access_token', 'custom_api_key', 'custom_api_secret', 'shopify_refresh_token', 'shopify_token_expires_at', 'created_at', 'updated_at')
        read_only_fields = ('id', 'shopify_refresh_token', 'shopify_token_expires_at', 'created_at', 'updated_at')
