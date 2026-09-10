from django.core.cache import cache
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver
from EmailMarketing.models import EmailSegment
from EmailMarketing.Serializer.AudienceSerializer import (
    AudienceEstimateSerializer,
    EmailSegmentSerializer,
    EmailSegmentListSerializer,
    EmailSegmentDetailSerializer,
)
from EmailMarketing.Views.base import StoreAuthenticatedMixin


def get_segment_cache_version(store_id):
    try:
        return cache.get(f"seg_ver_{store_id}", 1)
    except Exception:
        return 1


def invalidate_segment_cache(store_id):
    try:
        cache.incr(f"seg_ver_{store_id}")
    except Exception:
        try:
            cache.set(f"seg_ver_{store_id}", 2)
        except Exception:
            pass


class EmailSegmentListCreateView(StoreAuthenticatedMixin, ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return EmailSegmentSerializer
        return EmailSegmentListSerializer

    def get_queryset(self):
        return EmailSegment.objects.filter(store=self.request.store).only(
            "id", "name", "description", "is_default", "cached_contact_count",
            "filter_config", "created_at", "updated_at"
        ).order_by("-created_at")

    def perform_create(self, serializer):
        segment = serializer.save(store=self.request.store)
        segment.cached_contact_count = AudienceResolver(self.request.store).count(segment.filter_config)
        segment.save(update_fields=["cached_contact_count"])
        invalidate_segment_cache(self.request.store.id)

    def get(self, request, *args, **kwargs):
        store_id = request.META.get("HTTP_X_STORE_ID") or request.GET.get("store_id")
        if not store_id:
            return Response(
                {"detail": "Store ID is required in headers (X-Store-Id) or query parameters."},
                status=status.HTTP_400_BAD_REQUEST
            )

        store = request.store
        if not store:
            return Response(
                {"detail": "Store context not found or access denied."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Store-scoped versioned caching for sub-10ms response
        ver = get_segment_cache_version(store.id)
        cache_key = f"seg_list_{store.id}_v{ver}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        response = super().get(request, *args, **kwargs)
        if response.status_code == 200:
            cache.set(cache_key, response.data, 60)
        return response


class EmailSegmentDetailView(StoreAuthenticatedMixin, RetrieveUpdateDestroyAPIView):
    serializer_class = EmailSegmentDetailSerializer

    def get_queryset(self):
        return EmailSegment.objects.filter(store=self.request.store)

    def perform_update(self, serializer):
        serializer.save()
        if hasattr(self.request, "store") and self.request.store:
            invalidate_segment_cache(self.request.store.id)

    def perform_destroy(self, instance):
        store_id = instance.store_id
        super().perform_destroy(instance)
        if store_id:
            invalidate_segment_cache(store_id)


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
