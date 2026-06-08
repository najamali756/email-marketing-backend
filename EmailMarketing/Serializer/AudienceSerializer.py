from rest_framework import serializers

from EmailMarketing.models import EmailSegment


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
