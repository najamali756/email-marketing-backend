import os

from django.contrib import admin
from django.core.management import call_command
from django.http import JsonResponse
from django.urls import include, path, re_path

from django.conf import settings
from django.conf.urls.static import static

def health_check(_request):
    return JsonResponse({"status": "ok", "service": "email-marketing"})



urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check),
    re_path(r"^auth/", include("Accounts.urls")),
    re_path(r"^stores/", include("Accounts.store_urls")),
    re_path(r"^emailMarketing/", include("EmailMarketing.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

try:
    if os.environ.get("production") == "True":
        call_command("migrate")
except Exception as exc:
    print(str(exc))
