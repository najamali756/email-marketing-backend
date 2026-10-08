import secrets
from django.contrib.auth.hashers import identify_hasher
from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db.models import Q
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from Accounts.models import Client, ClientUser, Store, User, UserRoleEnum
from Accounts.serializers import (
    ClientSerializer,
    StoreSerializer,
    UserSerializer,
    build_client_access_list,
)

SSO_TOKEN_SALT = "sso_external_temp_token"
TEMP_TOKEN_EXPIRY_SECONDS = 300  # 5 minutes


def set_user_password(user, password_val):
    """
    Supports both raw passwords and imported Django password hashes.
    If password_val is already a Django hash (e.g. pbkdf2_sha256$...),
    it assigns it directly without double-hashing.
    """
    if not password_val:
        return
    try:
        identify_hasher(password_val)
        # Valid Django hash format: assign directly to support hash import
        user.password = password_val
    except (ValueError, Exception):
        # Plain text password: hash via standard set_password
        user.set_password(password_val)


class ExternalUserCreateView(APIView):
    """
    API 1: External User Provisioning & Linking
    Creates a new user or grants an existing user access to the store's client.
    Supports importing existing Django password hashes and user details.
    
    Required payload:
      - external_token: The store's third_party_token
      - username (or email): Username / email for the user
      - password: Password (plain text or Django hash)
    Optional payload:
      - role: 'member' or 'admin' (default: 'member')
      - first_name, last_name
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        external_token = (
            request.data.get("external_token")
            or request.data.get("store_token")
            or request.data.get("third_party_token")
        )
        username = request.data.get("username") or request.data.get("email")
        password = request.data.get("password")
        role = request.data.get("role", UserRoleEnum.member.value)
        first_name = request.data.get("first_name", "")
        last_name = request.data.get("last_name", "")

        if not external_token:
            return Response(
                {
                    "status": "error",
                    "message": "external_token is required.",
                    "detail": "external_token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not username:
            return Response(
                {
                    "status": "error",
                    "message": "username or email is required.",
                    "detail": "username or email is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        username = str(username).strip()
        store = (
            Store.objects.filter(third_party_token=external_token, is_active=True)
            .select_related("client")
            .first()
        )
        if not store:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid or inactive store external token.",
                    "detail": "Invalid or inactive store external token.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        client = store.client
        if not client or not client.is_active:
            return Response(
                {
                    "status": "error",
                    "message": "Store is not associated with an active client.",
                    "detail": "Store is not associated with an active client.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(
            Q(email__iexact=username) | Q(username__iexact=username)
        ).first()

        assigned_role = (
            UserRoleEnum.admin.value if role == "admin" else UserRoleEnum.member.value
        )

        if user:
            # User already exists - ensure active access to store and client
            client_user, created_cu = ClientUser.objects.get_or_create(
                user=user,
                client=client,
                defaults={"role": assigned_role, "is_active": True},
            )
            if not created_cu and not client_user.is_active:
                client_user.is_active = True
                client_user.save(update_fields=["is_active", "updated_at"])

            user.assigned_stores.add(store)

            update_fields = []
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                update_fields.append("first_name")
            if last_name and user.last_name != last_name:
                user.last_name = last_name
                update_fields.append("last_name")

            if password:
                set_user_password(user, password)
                update_fields.append("password")

            if update_fields:
                update_fields.append("updated_at")
                user.save(update_fields=update_fields)

            return Response(
                {
                    "status": "success",
                    "message": "User access granted to store and client successfully.",
                },
                status=status.HTTP_200_OK,
            )

        # New user creation
        if not password:
            return Response(
                {
                    "status": "error",
                    "message": "password is required to create a new user.",
                    "detail": "password is required to create a new user.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User(
            email=username,
            username=username,
            first_name=first_name,
            last_name=last_name,
            user_type="operator",
        )
        set_user_password(user, password)
        user.save()

        ClientUser.objects.create(
            user=user,
            client=client,
            role=assigned_role,
            is_active=True,
        )
        user.assigned_stores.add(store)

        return Response(
            {
                "status": "success",
                "message": "User created and assigned to store successfully.",
            },
            status=status.HTTP_201_CREATED,
        )


class ExternalGenerateTempTokenView(APIView):
    """
    API 2: Temporary Token Generation
    Generates a cryptographically signed, short-lived single-use temporary token
    for SSO login redirect.
    
    Required payload:
      - external_token: The store's third_party_token
      - username (or email): Username / email of the user
    Optional payload:
      - password: User password (plain text or Django hash)
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        external_token = (
            request.data.get("external_token")
            or request.data.get("store_token")
            or request.data.get("third_party_token")
        )
        username = request.data.get("username") or request.data.get("email")
        password = request.data.get("password")

        if not external_token:
            return Response(
                {
                    "status": "error",
                    "message": "external_token is required.",
                    "detail": "external_token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not username:
            return Response(
                {
                    "status": "error",
                    "message": "username or email is required.",
                    "detail": "username or email is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        username = str(username).strip()
        store = (
            Store.objects.filter(third_party_token=external_token, is_active=True)
            .select_related("client")
            .first()
        )
        if not store:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid or inactive store external token.",
                    "detail": "Invalid or inactive store external token.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        user = User.objects.filter(
            Q(email__iexact=username) | Q(username__iexact=username),
            is_active=True,
        ).first()
        if not user:
            return Response(
                {
                    "status": "error",
                    "message": "User not found or inactive.",
                    "detail": "User not found or inactive.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if password:
            is_valid = False
            if password == user.password:
                is_valid = True
            elif user.check_password(password):
                is_valid = True

            if not is_valid:
                return Response(
                    {
                        "status": "error",
                        "message": "Invalid credentials for user.",
                        "detail": "Invalid credentials for user.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Verify user has access to store's client or is assigned to store
        has_client_access = ClientUser.objects.filter(
            user=user, client=store.client, is_active=True
        ).exists()
        has_store_access = (
            user.assigned_stores.filter(id=store.id).exists()
            or user.is_staff
            or user.is_superuser
        )

        if not (has_client_access or has_store_access):
            return Response(
                {
                    "status": "error",
                    "message": "User does not have access to this store.",
                    "detail": "User does not have access to this store.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        signer = TimestampSigner(salt=SSO_TOKEN_SALT)
        nonce = secrets.token_hex(16)
        payload = f"{user.id}:{store.id}:{nonce}"
        temp_token = signer.sign(payload)

        # Store nonce in cache to track active validity
        cache.set(
            f"sso_temp_active_{nonce}",
            {"user_id": user.id, "store_id": store.id},
            timeout=TEMP_TOKEN_EXPIRY_SECONDS,
        )

        return Response(
            {
                "status": "success",
                "temp_token": temp_token,
                "expires_in": TEMP_TOKEN_EXPIRY_SECONDS,
                "store_id": store.id,
                "store_name": store.name,
                "redirect_param": f"?token={temp_token}",
            },
            status=status.HTTP_200_OK,
        )


class ExternalExchangeTempTokenView(APIView):
    """
    API 3: Temporary Token Exchange for Real Session
    Exchanges the single-use temporary token for a real DRF authentication token
    and complete login session data.
    
    The temp token is strictly single-use and invalid for regular API authorization.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def _exchange(self, request):
        temp_token = (
            request.data.get("temp_token")
            or request.data.get("token")
            or request.query_params.get("token")
            or request.query_params.get("temp_token")
        )

        if not temp_token:
            return Response(
                {
                    "status": "error",
                    "message": "Temporary token is required.",
                    "detail": "Temporary token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        signer = TimestampSigner(salt=SSO_TOKEN_SALT)
        try:
            raw_payload = signer.unsign(temp_token, max_age=TEMP_TOKEN_EXPIRY_SECONDS)
        except SignatureExpired:
            return Response(
                {
                    "status": "error",
                    "message": "Temporary login token has expired. Please request a new one.",
                    "detail": "Temporary login token has expired. Please request a new one.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BadSignature:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid temporary login token.",
                    "detail": "Invalid temporary login token.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            parts = raw_payload.split(":")
            user_id = int(parts[0])
            store_id = int(parts[1])
            nonce = parts[2]
        except (IndexError, ValueError):
            return Response(
                {
                    "status": "error",
                    "message": "Malformed token structure.",
                    "detail": "Malformed token structure.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check and enforce single-use replay protection
        used_key = f"sso_token_used_{nonce}"
        if cache.get(used_key):
            return Response(
                {
                    "status": "error",
                    "message": "This temporary login token has already been used.",
                    "detail": "This temporary login token has already been used.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        cache.set(used_key, True, timeout=TEMP_TOKEN_EXPIRY_SECONDS)
        cache.delete(f"sso_temp_active_{nonce}")

        user = User.objects.filter(id=user_id, is_active=True).first()
        if not user:
            return Response(
                {
                    "status": "error",
                    "message": "User not found or account is deactivated.",
                    "detail": "User not found or account is deactivated.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        store = (
            Store.objects.filter(id=store_id, is_active=True)
            .select_related("client")
            .first()
        )

        auth_token, _ = Token.objects.get_or_create(user=user)
        user_type = "staff" if (user.is_staff or user.is_superuser) else user.user_type

        return Response(
            {
                "token": auth_token.key,
                "user": {
                    **UserSerializer(user).data,
                    "user_type": user_type,
                },
                "clients": build_client_access_list(user),
                "store_id": store.id if store else None,
                "store_name": store.name if store else None,
                "client_id": store.client.id if (store and store.client) else None,
                "client_name": store.client.name if (store and store.client) else None,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        return self._exchange(request)

    def get(self, request):
        return self._exchange(request)
