from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.models import EmailBrandSettings
from EmailMarketing.Serializer.TemplateSerializer import EmailBrandSettingsSerializer
from EmailMarketing.Views.base import StoreAuthenticatedMixin


class EmailBrandSettingsView(StoreAuthenticatedMixin, APIView):
    def get(self, request):
        settings_obj, _ = EmailBrandSettings.objects.get_or_create(store=request.store)
        return Response(EmailBrandSettingsSerializer(settings_obj).data)

    def put(self, request):
        settings_obj, _ = EmailBrandSettings.objects.get_or_create(store=request.store)
        serializer = EmailBrandSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
