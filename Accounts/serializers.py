from django.contrib.auth import authenticate
from django.db import transaction
from rest_framework import serializers
from rest_framework.authtoken.models import Token

from Accounts.models import Client, ClientUser, User, UserRoleEnum, Store, Contact


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ("id", "name", "is_active", "created_at")


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = (
            "id",
            "name",
            "shop_url",
            "is_active",
            "email_provider",
            "sendgrid_api_key",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_use_tls",
            "default_from_email",
            "default_from_name",
            "api_key",
            "created_at",
        )
        read_only_fields = ("api_key", "created_at")
        extra_kwargs = {
            "sendgrid_api_key": {"write_only": True},
            "smtp_password": {"write_only": True},
        }


class StoreCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = (
            "name",
            "shop_url",
            "email_provider",
            "sendgrid_api_key",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_use_tls",
            "default_from_email",
            "default_from_name",
        )


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = (
            "id",
            "external_id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "city",
            "country",
            "tags",
            "accept_email_marketing",
            "total_orders",
            "total_spent",
            "last_order_at",
            "created_at",
            "updated_at",
        )


class ContactUpsertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = (
            "external_id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "city",
            "country",
            "tags",
            "accept_email_marketing",
            "total_orders",
            "total_spent",
            "last_order_at",
        )


class UserSerializer(serializers.ModelSerializer):
    assigned_stores = serializers.PrimaryKeyRelatedField(many=True, queryset=Store.objects.all(), required=False)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "user_type",
            "assigned_stores",
            "is_active",
            "is_staff",
            "is_superuser",
            "created_at"
        )
        read_only_fields = ("id", "is_staff", "is_superuser", "created_at")


class ClientUserSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = ClientUser
        fields = ("id", "user", "role", "is_active", "created_at")


class ClientAccessSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    role = serializers.CharField()
    is_active = serializers.BooleanField()
    stores = StoreSerializer(many=True)


def build_client_access_list(user):
    result = []
    if user.is_staff or user.is_superuser:
        clients = Client.objects.filter(is_active=True).order_by("name")
        for client in clients:
            stores = Store.objects.filter(client=client, is_active=True).order_by("name")
            result.append({
                "id": client.id,
                "name": client.name,
                "role": "staff",
                "is_active": client.is_active,
                "stores": StoreSerializer(stores, many=True).data,
            })
    else:
        memberships = (
            ClientUser.objects.filter(user=user, is_active=True, client__is_active=True)
            .select_related("client")
            .order_by("client__name")
        )
        for membership in memberships:
            client = membership.client
            if user.user_type == "admin":
                stores = Store.objects.filter(client=client, is_active=True).order_by("name")
            else:
                stores = user.assigned_stores.filter(client=client, is_active=True).order_by("name")

            result.append({
                "id": client.id,
                "name": client.name,
                "role": user.user_type,
                "is_active": client.is_active,
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
                user_type="admin",
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

        has_access = user.is_staff or user.is_superuser or ClientUser.objects.filter(
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
    user_type = serializers.ChoiceField(choices=[("admin", "Admin"), ("operator", "Operator")], default="operator")
    assigned_stores = serializers.PrimaryKeyRelatedField(many=True, queryset=Store.objects.all(), required=False)

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
        assigned_stores = validated_data.pop("assigned_stores", [])

        if existing_user:
            # Update user type and assigned stores if they exist
            existing_user.user_type = validated_data.get("user_type", existing_user.user_type)
            if assigned_stores:
                existing_user.assigned_stores.set(assigned_stores)
            existing_user.save()
            
            return ClientUser.objects.create(
                user=existing_user,
                client=client,
                role=validated_data["user_type"],
            )

        with transaction.atomic():
            user = User.objects.create_user(
                email=validated_data["email"],
                password=validated_data["password"],
                first_name=validated_data.get("first_name", ""),
                last_name=validated_data.get("last_name", ""),
                user_type=validated_data["user_type"],
            )
            if assigned_stores:
                user.assigned_stores.set(assigned_stores)
                
            return ClientUser.objects.create(
                user=user,
                client=client,
                role=validated_data["user_type"],
            )
