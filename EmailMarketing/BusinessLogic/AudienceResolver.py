from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from EmailMarketing.models import EmailUnsubscribe
from Accounts.models import Contact


class AudienceResolver:
    def __init__(self, store):
        self.store = store

    def base_queryset(self):
        unsubscribed_emails = EmailUnsubscribe.objects.filter(store=self.store).values_list("email", flat=True)
        return (
            Contact.objects.filter(store=self.store, accept_email_marketing=True)
            .exclude(email="")
            .exclude(email__isnull=True)
            .exclude(email__in=unsubscribed_emails)
        )

    def apply_filters(self, queryset, filter_config=None):
        filter_config = filter_config or {}

        tags = filter_config.get("tags") or []
        if tags:
            for tag in tags:
                queryset = queryset.filter(tags__contains=[tag])

        min_total_spent = filter_config.get("min_total_spent")
        if min_total_spent is not None:
            queryset = queryset.filter(total_spent__gte=min_total_spent)

        min_orders = filter_config.get("min_orders")
        if min_orders is not None:
            queryset = queryset.filter(total_orders__gte=min_orders)

        purchase_count = filter_config.get("purchase_count")
        if purchase_count is not None:
            queryset = queryset.filter(total_orders=purchase_count)

        last_purchase_days = filter_config.get("last_purchase_days")
        if last_purchase_days is not None:
            cutoff = timezone.now() - timedelta(days=int(last_purchase_days))
            queryset = queryset.filter(last_order_at__gte=cutoff)

        inactive_days = filter_config.get("inactive_days")
        if inactive_days is not None:
            cutoff = timezone.now() - timedelta(days=int(inactive_days))
            queryset = queryset.filter(Q(last_order_at__lt=cutoff) | Q(last_order_at__isnull=True))

        location = filter_config.get("location")
        if location:
            queryset = queryset.filter(Q(city__icontains=location) | Q(country__icontains=location))

        return queryset

    def resolve(self, filter_config=None):
        return self.apply_filters(self.base_queryset(), filter_config)

    def count(self, filter_config=None):
        return self.resolve(filter_config).count()

    def estimate_breakdown(self, filter_config=None):
        queryset = self.resolve(filter_config)
        total = queryset.count()
        if total == 0:
            return {
                "total": 0,
                "new_customers": 0,
                "repeat_buyers": 0,
                "vip_members": 0,
                "inactive": 0,
            }

        repeat_buyers = queryset.filter(total_orders__gte=2).count()
        vip_members = queryset.filter(total_spent__gte=500).count()
        new_customers = queryset.filter(total_orders__lte=1).count()
        inactive = max(total - repeat_buyers - new_customers, 0)

        return {
            "total": total,
            "new_customers": new_customers,
            "repeat_buyers": repeat_buyers,
            "vip_members": vip_members,
            "inactive": inactive,
        }
