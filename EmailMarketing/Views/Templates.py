from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView

from EmailMarketing.models import EmailTemplate
from EmailMarketing.Serializer.TemplateSerializer import EmailTemplateSerializer
from EmailMarketing.Views.base import StoreAuthenticatedMixin


class EmailTemplateListCreateView(StoreAuthenticatedMixin, ListCreateAPIView):
    serializer_class = EmailTemplateSerializer

    def get_queryset(self):
        queryset = EmailTemplate.objects.filter(store=self.request.store, is_active=True)
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)
        return queryset.order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(store=self.request.store)


class EmailTemplateDetailView(StoreAuthenticatedMixin, RetrieveUpdateAPIView):
    serializer_class = EmailTemplateSerializer

    def get_queryset(self):
        return EmailTemplate.objects.filter(store=self.request.store)
