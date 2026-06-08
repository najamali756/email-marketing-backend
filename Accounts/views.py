from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Accounts.mixins import ClientContextMixin
from Accounts.permissions import IsClientAdmin
from Accounts.serializers import (
    ClientSerializer,
    ClientUserSerializer,
    InviteUserSerializer,
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
    build_client_access_list,
)
from Stores.models import Store
from Stores.serializers import StoreSerializer


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

        return Response({
            "token": serializer.validated_data["token"],
            "user": UserSerializer(user).data,
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
        return Response({
            "user": UserSerializer(request.user).data,
            "clients": build_client_access_list(request.user),
        })


class UserListCreateView(ClientContextMixin, APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated(), IsClientAdmin()]
        return [IsAuthenticated()]

    def get(self, request):
        memberships = (
            ClientUser.objects.filter(client=request.client, is_active=True)
            .select_related("user")
            .order_by("-created_at")
        )
        return Response(ClientUserSerializer(memberships, many=True).data)

    def post(self, request):
        serializer = InviteUserSerializer(data=request.data, context={"client": request.client})
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        return Response(
            ClientUserSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )
