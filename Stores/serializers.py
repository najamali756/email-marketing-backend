from rest_framework import serializers

from Stores.models import Contact, Store


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
