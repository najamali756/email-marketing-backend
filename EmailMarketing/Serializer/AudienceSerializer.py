from rest_framework import serializers

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
        # Keep filter_config lightweight for list/table display by excluding massive member_emails
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

    def _get_emails_and_params(self, obj):
        request = self.context.get("request")
        fc = obj.filter_config or {}
        emails = fc.get("member_emails") or []

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

        total = len(emails)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        offset = (page - 1) * page_size
        page_emails = emails[offset:offset + page_size]
        return emails, page_emails, page, page_size, total, total_pages

    def get_members(self, obj):
        _, page_emails, _, _, _, _ = self._get_emails_and_params(obj)
        if not page_emails:
            return []
        from Accounts.models import Contact
        contacts = Contact.objects.filter(store=obj.store, email__in=page_emails).values(
            "id", "email", "first_name", "last_name", "total_orders", "total_spent", "accept_email_marketing"
        )
        return list(contacts)

    def get_pagination(self, obj):
        _, _, page, page_size, total, total_pages = self._get_emails_and_params(obj)
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
