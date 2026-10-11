from django.urls import path, re_path
from automation.views import (
    AutomationOpenTrackingView,
    AutomationStatsView,
    AutomationViewSet,
    NotificationSendHistoryView,
    TriggerNotificationView,
    BrowseAbandonmentEventView,
)

urlpatterns = [
    path("", AutomationViewSet.as_view({"get": "list", "post": "create"}), name="automation-list"),
    path("<int:pk>/", AutomationViewSet.as_view({"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}), name="automation-detail"),
    path("stats/", AutomationStatsView.as_view(), name="automation-stats"),
    path("history/", NotificationSendHistoryView.as_view(), name="automation-history"),
    path("trigger/", TriggerNotificationView.as_view(), name="automation-trigger"),
    path("browse-abandonment/", BrowseAbandonmentEventView.as_view(), name="automation-browse-abandonment"),
    re_path(r"^track/open/?$", AutomationOpenTrackingView.as_view(), name="automation-track-open"),
]
