from rest_framework import status
from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver
from EmailMarketing.models import EmailSegment
from EmailMarketing.Serializer.AudienceSerializer import AudienceEstimateSerializer, EmailSegmentSerializer
from EmailMarketing.Views.base import StoreAuthenticatedMixin


class EmailSegmentListCreateView(StoreAuthenticatedMixin, ListCreateAPIView):
    serializer_class = EmailSegmentSerializer

    def get_queryset(self):
        return EmailSegment.objects.filter(store=self.request.store).order_by("-created_at")

    def perform_create(self, serializer):
        segment = serializer.save(store=self.request.store)
        segment.cached_contact_count = AudienceResolver(self.request.store).count(segment.filter_config)
        segment.save(update_fields=["cached_contact_count"])


class AudienceEstimateView(StoreAuthenticatedMixin, APIView):
    def post(self, request):
        serializer = AudienceEstimateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        filter_config = serializer.validated_data.get("filter_config") or {}
        segment_id = serializer.validated_data.get("segment_id")
        if segment_id:
            segment = EmailSegment.objects.filter(id=segment_id, store=request.store).first()
            if not segment:
                return Response({"detail": "Segment not found."}, status=status.HTTP_404_NOT_FOUND)
            filter_config = segment.filter_config or {}

        resolver = AudienceResolver(request.store)
        return Response({
            "estimated_recipients": resolver.count(filter_config),
            "audience_breakdown": resolver.estimate_breakdown(filter_config),
        })
