from django.contrib.auth import authenticate
from django.db import transaction
from rest_framework import serializers
from rest_framework.authtoken.models import Token

from Accounts.models import Client, ClientUser, User, UserRoleEnum
from Stores.models import Store
from Stores.serializers import StoreSerializer


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ("id", "name", "is_active", "created_at")


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "is_active", "created_at")
        read_only_fields = ("id", "created_at")


class ClientUserSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = ClientUser
        fields = ("id", "user", "role", "is_active", "created_at")


class ClientAccessSerializer(serializers.Serializer):
    """Client + role + stores for login/me responses."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    role = serializers.CharField()
    is_active = serializers.BooleanField()
    stores = StoreSerializer(many=True)


def build_client_access_list(user):
    memberships = (
        ClientUser.objects.filter(user=user, is_active=True, client__is_active=True)
        .select_related("client")
        .order_by("client__name")
    )
    result = []
    for membership in memberships:
        stores = Store.objects.filter(client=membership.client, is_active=True)
        result.append({
            "id": membership.client.id,
            "name": membership.client.name,
            "role": membership.role,
            "is_active": membership.client.is_active,
            "stores": StoreSerializer(stores, many=True).data,
        })
    return result


class RegisterSerializer(serializers.Serializer):
    client_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    store_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    shop_url = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def create(self, validated_data):
        store_name = validated_data.pop("store_name", "").strip()
        shop_url = validated_data.pop("shop_url", "").strip()

        with transaction.atomic():
            client = Client.objects.create(name=validated_data["client_name"])
            user = User.objects.create_user(
                email=validated_data["email"],
                password=validated_data["password"],
                first_name=validated_data.get("first_name", ""),
                last_name=validated_data.get("last_name", ""),
            )
            ClientUser.objects.create(
                user=user,
                client=client,
                role=UserRoleEnum.admin.value,
            )
            store = None
            if store_name:
                store = Store.objects.create(
                    client=client,
                    name=store_name,
                    shop_url=shop_url or f"{store_name.lower().replace(' ', '-')}.local",
                )

        token, _ = Token.objects.get_or_create(user=user)
        return {
            "token": token.key,
            "user": user,
            "client": client,
            "store": store,
        }


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["email"].lower(),
            password=attrs["password"],
        )
        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid email or password.")

        has_access = user.is_superuser or ClientUser.objects.filter(
            user=user, is_active=True, client__is_active=True
        ).exists()
        if not has_access:
            raise serializers.ValidationError("User has no client access.")

        token, _ = Token.objects.get_or_create(user=user)
        attrs["user"] = user
        attrs["token"] = token.key
        return attrs


class InviteUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    role = serializers.ChoiceField(
        choices=[UserRoleEnum.admin.value, UserRoleEnum.member.value],
        default=UserRoleEnum.member.value,
    )

    def validate(self, attrs):
        email = attrs["email"].lower()
        attrs["email"] = email
        client = self.context["client"]
        existing_user = User.objects.filter(email__iexact=email).first()

        if existing_user:
            if ClientUser.objects.filter(user=existing_user, client=client).exists():
                raise serializers.ValidationError({"email": "User already has access to this client."})
            attrs["existing_user"] = existing_user
            return attrs

        if not attrs.get("password"):
            raise serializers.ValidationError({"password": "Password is required for new users."})
        return attrs

    def create(self, validated_data):
        client = self.context["client"]
        existing_user = validated_data.pop("existing_user", None)

        if existing_user:
            return ClientUser.objects.create(
                user=existing_user,
                client=client,
                role=validated_data["role"],
            )

        with transaction.atomic():
            user = User.objects.create_user(
                email=validated_data["email"],
                password=validated_data["password"],
                first_name=validated_data.get("first_name", ""),
                last_name=validated_data.get("last_name", ""),
            )
            return ClientUser.objects.create(
                user=user,
                client=client,
                role=validated_data["role"],
            )
