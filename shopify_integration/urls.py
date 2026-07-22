from django.urls import path
from shopify_integration.views import (
    ShopifySettingsView, 
    ShopifyInstallView, 
    ShopifyCallbackView,
    ShopifySubscribeAllView,
    ShopifySyncTriggerView,
    ShopifySegmentCreateView
)

urlpatterns = [
    path('settings/', ShopifySettingsView.as_view(), name='shopify-settings'),
    path('install/', ShopifyInstallView.as_view(), name='shopify-install'),
    path('callback/', ShopifyCallbackView.as_view(), name='shopify-callback'),
    path('contacts/subscribe-all/', ShopifySubscribeAllView.as_view(), name='shopify-subscribe-all'),
    path('sync/trigger/', ShopifySyncTriggerView.as_view(), name='shopify-sync-trigger'),
    path('segments/create/', ShopifySegmentCreateView.as_view(), name='shopify-segments-create'),
]
