from django.urls import path, re_path
from shopify_integration.views import (
    ShopifySettingsView, 
    ShopifyInstallView, 
    ShopifyCallbackView,
    ShopifySubscribeAllView,
    ShopifySyncTriggerView,
    ShopifySingleSegmentSyncView,
    ShopifySegmentsSyncAllView,
    ShopifySegmentCreateView,
    ShopifyWebhookView
)

from shopify_integration.webhooks import (
    ShopifyWebhookRegisterView,
    ShopifyWebhookReceiverView,
)
from shopify_integration.views_pixel import ShopifyPixelEventIngestionView

urlpatterns = [
    path('settings/', ShopifySettingsView.as_view(), name='shopify-settings'),
    path('install/', ShopifyInstallView.as_view(), name='shopify-install'),
    path('callback/', ShopifyCallbackView.as_view(), name='shopify-callback'),
    path('contacts/subscribe-all/', ShopifySubscribeAllView.as_view(), name='shopify-subscribe-all'),
    path('sync/trigger/', ShopifySyncTriggerView.as_view(), name='shopify-sync-trigger'),
    path('sync/segments/', ShopifySegmentsSyncAllView.as_view(), name='shopify-sync-segments-all'),
    path('segments/<int:segment_id>/sync/', ShopifySingleSegmentSyncView.as_view(), name='shopify-single-segment-sync'),
    path('segments/create/', ShopifySegmentCreateView.as_view(), name='shopify-segments-create'),
    path('webhooks/register/', ShopifyWebhookRegisterView.as_view(), name='shopify-webhooks-register'),
    path('webhooks/receiver/', ShopifyWebhookReceiverView.as_view(), name='shopify-webhooks-receiver'),
    path('events/track', ShopifyPixelEventIngestionView.as_view(), name='shopify-events-track'),
    re_path(r'^webhooks/customers/data_request/?$', ShopifyWebhookView.as_view(), name='shopify-webhook-data-request'),
    re_path(r'^webhooks/customers/redact/?$', ShopifyWebhookView.as_view(), name='shopify-webhook-customer-redact'),
    re_path(r'^webhooks/shop/redact/?$', ShopifyWebhookView.as_view(), name='shopify-webhook-shop-redact'),
    re_path(r'^webhooks/?$', ShopifyWebhookView.as_view(), name='shopify-webhook-unified'),
]
