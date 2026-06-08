from django.core.management.base import BaseCommand

from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver
from EmailMarketing.models import EmailSegment
from Stores.models import Store

DEFAULT_SEGMENTS = [
    {"name": "All Subscribers", "description": "All opted-in contacts", "is_default": True, "filter_config": {}},
    {"name": "VIP Customers", "description": "Total spend above 500", "filter_config": {"min_total_spent": 500}},
    {"name": "New This Month", "description": "Purchased in last 30 days", "filter_config": {"last_purchase_days": 30}},
    {"name": "Inactive 60+ Days", "description": "No purchase in 60 days", "filter_config": {"inactive_days": 60}},
    {"name": "Repeat Buyers", "description": "2+ orders", "filter_config": {"min_orders": 2}},
]


class Command(BaseCommand):
    help = "Seed default email segments for a store"

    def add_arguments(self, parser):
        parser.add_argument("store_id", type=int)

    def handle(self, *args, **options):
        store = Store.objects.filter(id=options["store_id"]).first()
        if not store:
            self.stderr.write("Store not found")
            return

        resolver = AudienceResolver(store)
        for segment_data in DEFAULT_SEGMENTS:
            segment, created = EmailSegment.objects.get_or_create(
                store=store,
                name=segment_data["name"],
                defaults={
                    "description": segment_data["description"],
                    "filter_config": segment_data["filter_config"],
                    "is_default": segment_data.get("is_default", False),
                },
            )
            segment.cached_contact_count = resolver.count(segment.filter_config)
            segment.save(update_fields=["cached_contact_count"])
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} {segment.name}: {segment.cached_contact_count}")
