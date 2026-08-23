from django.utils import timezone
from django.db.models import Q
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView, RetrieveUpdateDestroyAPIView

from Accounts.mixins import ClientContextMixin, StoreContextMixin
from Accounts.permissions import IsClientAdmin, HasStoreContext
from Accounts.models import Client, ClientUser, User, Store, Contact
from Accounts.serializers import (
    ClientSerializer,
    ClientUserSerializer,
    InviteUserSerializer,
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
    StoreSerializer,
    StoreCreateSerializer,
    ContactSerializer,
    ContactUpsertSerializer,
    build_client_access_list,
)
from Accounts.BusinessLogic.CsvContactImporter import CsvContactImporter
from shopify_integration.sync import bulk_update_marketing_consent_shopify
from EmailMarketing.models import EmailSegment
from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        stores = Store.objects.filter(client=result["client"], is_active=True)
        payload = {
            "token": result["token"],
            "user": UserSerializer(result["user"]).data,
            "client": ClientSerializer(result["client"]).data,
            "clients": build_client_access_list(result["user"]),
            "stores": StoreSerializer(stores, many=True).data,
        }
        if result["store"]:
            payload["store"] = StoreSerializer(result["store"]).data
        return Response(payload, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        # If user is staff/admin/operator, return user_type and accessible clients/stores list
        user_type = "staff" if (user.is_staff or user.is_superuser) else user.user_type
        
        return Response({
            "token": serializer.validated_data["token"],
            "user": {
                **UserSerializer(user).data,
                "user_type": user_type,
            },
            "clients": build_client_access_list(user),
        })


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response({"detail": "Logged out."})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_type = "staff" if (user.is_staff or user.is_superuser) else user.user_type
        return Response({
            "user": {
                **UserSerializer(user).data,
                "user_type": user_type,
            },
            "clients": build_client_access_list(user),
        })


# Client Management endpoints (Staff only)
class ClientListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response({"detail": "Staff access required."}, status=status.HTTP_403_FORBIDDEN)
        clients = Client.objects.all().order_by("-created_at")
        return Response(ClientSerializer(clients, many=True).data)

    def post(self, request):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response({"detail": "Staff access required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ClientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ClientDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        return Client.objects.filter(id=pk).first()

    def patch(self, request, pk):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response({"detail": "Staff access required."}, status=status.HTTP_403_FORBIDDEN)
        client = self.get_object(pk)
        if not client:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ClientSerializer(client, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response({"detail": "Staff access required."}, status=status.HTTP_403_FORBIDDEN)
        client = self.get_object(pk)
        if not client:
            return Response({"detail": "Client not found."}, status=status.HTTP_404_NOT_FOUND)
        client.delete()
        return Response({"detail": "Client deleted."}, status=status.HTTP_204_NO_CONTENT)


# User Management endpoints (Admin / Staff)
class UserListCreateView(ClientContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsClientAdmin]

    def get(self, request):
        client_users = ClientUser.objects.filter(client=request.client, is_active=True).select_related("user")
        users = [cu.user for cu in client_users]
        return Response(UserSerializer(users, many=True).data)

    def post(self, request):
        serializer = InviteUserSerializer(data=request.data, context={"client": request.client})
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        return Response(UserSerializer(membership.user).data, status=status.HTTP_201_CREATED)


class UserDetailView(ClientContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsClientAdmin]

    def get_object(self, pk, client):
        membership = ClientUser.objects.filter(user_id=pk, client=client, is_active=True).first()
        return membership.user if membership else None

    def patch(self, request, pk):
        user_to_edit = self.get_object(pk, request.client)
        if not user_to_edit:
            return Response({"detail": "User not found under this client."}, status=status.HTTP_404_NOT_FOUND)

        serializer = UserSerializer(user_to_edit, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        edited_user = serializer.save()

        # Update password directly if provided
        password = request.data.get("password")
        if password:
            edited_user.set_password(password)
            edited_user.save()

        return Response(UserSerializer(edited_user).data)

    def delete(self, request, pk):
        user_to_delete = self.get_object(pk, request.client)
        if not user_to_delete:
            return Response({"detail": "User not found under this client."}, status=status.HTTP_404_NOT_FOUND)

        ClientUser.objects.filter(user=user_to_delete, client=request.client).delete()
        if not ClientUser.objects.filter(user=user_to_delete).exists():
            user_to_delete.delete()

        return Response({"detail": "User deleted."}, status=status.HTTP_204_NO_CONTENT)


# Store / Shop Management (Consolidated from Stores app)
class StoreListCreateView(ClientContextMixin, ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return StoreCreateSerializer
        return StoreSerializer

    def get_queryset(self):
        return Store.objects.filter(client=self.request.client, is_active=True).order_by("-created_at")

    def get_permissions(self):
        # Allow client admins/staff to create shops
        if self.request.method == "POST":
            return [IsAuthenticated(), IsClientAdmin()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        serializer.save(client=self.request.client)


class StoreDetailView(ClientContextMixin, RetrieveUpdateDestroyAPIView):
    serializer_class = StoreSerializer

    def get_queryset(self):
        return Store.objects.filter(client=self.request.client)

    def get_permissions(self):
        if self.request.method in ["PUT", "PATCH", "DELETE"]:
            return [IsAuthenticated(), IsClientAdmin()]
        return [IsAuthenticated()]


# Contacts & Synchronization (Consolidated from Stores app)
class ContactListCreateView(StoreContextMixin, APIView):
    permission_classes = [HasStoreContext]

    def get(self, request):
        qs = Contact.objects.filter(store=request.store).order_by("-created_at")

        # 1. Server-side Search
        search = request.GET.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(phone__icontains=search) |
                Q(city__icontains=search) |
                Q(country__icontains=search)
            )

        # 2. Server-side Status Filter (subscribed / unsubscribed)
        status_param = request.GET.get("status", "").strip().lower()
        if status_param == "subscribed":
            qs = qs.filter(accept_email_marketing=True)
        elif status_param == "unsubscribed":
            qs = qs.filter(accept_email_marketing=False)

        # 3. Server-side Segment Filter
        segment_id_param = request.GET.get("segment_id", "").strip()
        if segment_id_param:

            segment_obj = EmailSegment.objects.filter(store=request.store, id=segment_id_param).first()
            if segment_obj:
                filter_config = segment_obj.filter_config if isinstance(segment_obj.filter_config, dict) else {}
                member_emails = filter_config.get("member_emails", [])
                resolved_qs = AudienceResolver(request.store).resolve(filter_config)
                qs = qs.filter(Q(id__in=resolved_qs.values("id")) | Q(segments=segment_obj) | Q(email__in=member_emails)).distinct()

        # 4. Server-side Pagination
        try:
            page = int(request.GET.get("page", 1))
        except ValueError:
            page = 1

        try:
            page_size = int(request.GET.get("page_size", 20))
        except ValueError:
            page_size = 20

        total_count = qs.count()
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

        offset = (page - 1) * page_size
        page_qs = qs[offset:offset + page_size]

        serializer = ContactSerializer(page_qs, many=True)
        return Response({
            "count": total_count,
            "total_pages": total_pages,
            "current_page": page,
            "page_size": page_size,
            "results": serializer.data
        })

    def post(self, request):
        serializer = ContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(store=request.store)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ContactSubscribeAllView(StoreContextMixin, APIView):
    """
    Unified Consent Endpoint: Handles Subscribe & Unsubscribe for Single Contact, Selected Contacts, or Bulk Store Contacts.
    Accepts:
      - accept_email_marketing: bool (default True)
      - contact_id: int/str (optional single contact ID)
      - contact_ids: list (optional list of contact IDs)
    """
    permission_classes = [HasStoreContext]

    def post(self, request):
        accepts_marketing = request.data.get("accept_email_marketing", True)
        if isinstance(accepts_marketing, str):
            accepts_marketing = (accepts_marketing.lower() != "false")

        contact_id = request.data.get("contact_id")
        contact_ids = request.data.get("contact_ids") or []
        if contact_id:
            contact_ids.append(contact_id)

        updated_count = bulk_update_marketing_consent_shopify(
            request.store,
            accepts_marketing=accepts_marketing,
            contact_ids=contact_ids if contact_ids else None
        )

        action_str = "subscribed" if accepts_marketing else "unsubscribed"
        return Response({
            "detail": f"Successfully {action_str} {updated_count} contact(s) and synced with Shopify API.",
            "updated_count": updated_count,
            "accept_email_marketing": accepts_marketing
        })


class ContactDetailView(StoreContextMixin, RetrieveUpdateDestroyAPIView):
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
