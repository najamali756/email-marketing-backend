from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Accounts.mixins import ClientContextMixin
from Accounts.permissions import IsClientAdmin
from Stores.BusinessLogic.CsvContactImporter import CsvContactImporter
from Stores.mixins import StoreContextMixin
from Stores.models import Contact, Store
from Stores.permissions import HasStoreContext
from Stores.serializers import (
    ContactSerializer,
    ContactUpsertSerializer,
    StoreCreateSerializer,
    StoreSerializer,
)


class StoreListCreateView(ClientContextMixin, ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return StoreCreateSerializer
        return StoreSerializer

    def get_queryset(self):
        return Store.objects.filter(client=self.request.client, is_active=True).order_by("-created_at")

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsClientAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(client=self.request.client)


class StoreDetailView(ClientContextMixin, RetrieveUpdateAPIView):
    serializer_class = StoreSerializer

    def get_queryset(self):
        return Store.objects.filter(client=self.request.client)


class ContactListCreateView(StoreContextMixin, ListCreateAPIView):
    permission_classes = [HasStoreContext]
    serializer_class = ContactSerializer

    def get_queryset(self):
        return Contact.objects.filter(store=self.request.store).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(store=self.request.store)


class ContactDetailView(StoreContextMixin, RetrieveUpdateAPIView):
    permission_classes = [HasStoreContext]
    serializer_class = ContactSerializer

    def get_queryset(self):
        return Contact.objects.filter(store=self.request.store)


class ContactBulkUpsertView(StoreContextMixin, APIView):
    permission_classes = [HasStoreContext]

    def post(self, request):
        contacts_data = request.data.get("contacts", [])
        if not isinstance(contacts_data, list):
            return Response({"detail": "contacts must be a list."}, status=400)

        created = updated = 0
        for item in contacts_data:
            serializer = ContactUpsertSerializer(data=item)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            contact, was_created = Contact.objects.update_or_create(
                store=request.store,
                email=data["email"],
                defaults={
                    **data,
                    "accept_email_marketing_at": timezone.now()
                    if data.get("accept_email_marketing")
                    else None,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return Response({"created": created, "updated": updated, "total": created + updated})


class ContactCsvUploadView(StoreContextMixin, APIView):
    permission_classes = [HasStoreContext]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"detail": "CSV file is required. Use form field name 'file'."}, status=400)

        if not uploaded_file.name.lower().endswith(".csv"):
            return Response({"detail": "Only .csv files are supported."}, status=400)

        default_opt_in = request.query_params.get("default_accept_email_marketing", "true").lower() != "false"

        try:
            result = CsvContactImporter(
                store=request.store,
                default_accept_email_marketing=default_opt_in,
            ).import_file(uploaded_file)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except Exception as exc:
            return Response({"detail": f"Failed to process CSV: {exc}"}, status=400)

        return Response(result, status=status.HTTP_201_CREATED)
