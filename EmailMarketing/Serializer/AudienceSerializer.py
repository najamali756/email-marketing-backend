from rest_framework import serializers

from Accounts.models import Contact
from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver
from EmailMarketing.models import EmailSegment


class EmailSegmentListSerializer(serializers.ModelSerializer):
    shopify_query = serializers.SerializerMethodField()

    class Meta:
        model = EmailSegment
        fields = (
            "id", "name", "description", "is_default",
            "cached_contact_count", "shopify_query", "filter_config",
            "created_at", "updated_at",
        )

    def get_shopify_query(self, obj):
        return (obj.filter_config or {}).get("shopify_query") or ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        fc = instance.filter_config or {}
        data["filter_config"] = {
            "shopify_id": fc.get("shopify_id"),
            "shopify_query": fc.get("shopify_query") or "",
        }
        return data


class EmailSegmentDetailSerializer(serializers.ModelSerializer):
    members = serializers.SerializerMethodField()
    pagination = serializers.SerializerMethodField()

    class Meta:
        model = EmailSegment
        fields = (
            "id", "name", "description", "filter_config", "is_default",
            "cached_contact_count", "members", "pagination", "created_at", "updated_at",
        )

    def _get_queryset_and_params(self, obj):
        request = self.context.get("request")
        try:
            page = int(request.GET.get("page", 1)) if request else 1
        except (ValueError, TypeError):
            page = 1

        try:
            page_size = int(request.GET.get("page_size", 25)) if request else 25
        except (ValueError, TypeError):
            page_size = 25

        if page < 1:
            page = 1

        qs = AudienceResolver(obj.store).resolve(obj.filter_config)
        total = qs.count()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        offset = (page - 1) * page_size
        page_qs = qs[offset:offset + page_size]
        return page_qs, page, page_size, total, total_pages

    def get_members(self, obj):
        page_qs, _, _, _, _ = self._get_queryset_and_params(obj)
        contacts = page_qs.values(
            "id", "email", "first_name", "last_name", "total_orders", "total_spent", "accept_email_marketing"
        )
        return list(contacts)

    def get_pagination(self, obj):
        _, page, page_size, total, total_pages = self._get_queryset_and_params(obj)
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }


class EmailSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailSegment
        fields = (
            "id", "name", "description", "filter_config", "is_default",
            "cached_contact_count", "created_at", "updated_at",
        )


class AudienceEstimateSerializer(serializers.Serializer):
    segment_id = serializers.IntegerField(required=False)
    filter_config = serializers.JSONField(required=False)
