import csv
import io
from decimal import Decimal, InvalidOperation

from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.utils import timezone

from Stores.models import Contact

REQUIRED_COLUMN = "email"

COLUMN_ALIASES = {
    "email": {"email", "email_address", "e-mail", "customer_email"},
    "first_name": {"first_name", "firstname", "first name", "fname"},
    "last_name": {"last_name", "lastname", "last name", "lname"},
    "phone": {"phone", "phone_number", "mobile", "contact_number"},
    "city": {"city", "town"},
    "country": {"country", "country_code"},
    "tags": {"tags", "tag", "customer_tags"},
    "accept_email_marketing": {
        "accept_email_marketing",
        "email_marketing",
        "subscribed",
        "marketing_opt_in",
        "opt_in",
    },
    "total_orders": {"total_orders", "orders", "order_count"},
    "total_spent": {"total_spent", "spent", "lifetime_spent", "total_spend"},
    "external_id": {"external_id", "customer_id", "id"},
}


def _normalize_header(value):
    return (value or "").strip().lower().replace("-", "_")


def _map_headers(fieldnames):
    mapping = {}
    normalized_fields = {_normalize_header(name): name for name in fieldnames if name}

    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            normalized_alias = _normalize_header(alias)
            if normalized_alias in normalized_fields:
                mapping[target] = normalized_fields[normalized_alias]
                break

    if REQUIRED_COLUMN not in mapping:
        raise ValueError(f"CSV must include an '{REQUIRED_COLUMN}' column.")

    return mapping


def _parse_bool(value, default=True):
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "subscribed", "opt_in", "opt-in"}


def _parse_tags(value):
    if not value or not str(value).strip():
        return []
    return [tag.strip() for tag in str(value).split(",") if tag.strip()]


def _parse_int(value, default=0):
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return default


def _parse_decimal(value, default=Decimal("0")):
    if value is None or str(value).strip() == "":
        return default
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default


class CsvContactImporter:
    def __init__(self, store, default_accept_email_marketing=True):
        self.store = store
        self.default_accept_email_marketing = default_accept_email_marketing

    def import_file(self, uploaded_file):
        raw = uploaded_file.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV file is empty or missing a header row.")

        column_map = _map_headers(reader.fieldnames)

        created = updated = skipped = 0
        errors = []

        for row_number, row in enumerate(reader, start=2):
            email = (row.get(column_map["email"]) or "").strip().lower()
            if not email:
                skipped += 1
                errors.append({"row": row_number, "error": "Missing email"})
                continue

            try:
                validate_email(email)
            except ValidationError:
                skipped += 1
                errors.append({"row": row_number, "error": f"Invalid email: {email}"})
                continue

            accept_email_marketing = _parse_bool(
                row.get(column_map.get("accept_email_marketing")),
                default=self.default_accept_email_marketing,
            )

            defaults = {
                "first_name": (row.get(column_map.get("first_name")) or "").strip() or None,
                "last_name": (row.get(column_map.get("last_name")) or "").strip() or None,
                "phone": (row.get(column_map.get("phone")) or "").strip() or None,
                "city": (row.get(column_map.get("city")) or "").strip() or None,
                "country": (row.get(column_map.get("country")) or "").strip() or None,
                "tags": _parse_tags(row.get(column_map.get("tags"))),
                "accept_email_marketing": accept_email_marketing,
                "accept_email_marketing_at": timezone.now() if accept_email_marketing else None,
                "total_orders": _parse_int(row.get(column_map.get("total_orders"))),
                "total_spent": _parse_decimal(row.get(column_map.get("total_spent"))),
            }

            if column_map.get("external_id"):
                external_id = (row.get(column_map["external_id"]) or "").strip() or None
                if external_id:
                    defaults["external_id"] = external_id

            _, was_created = Contact.objects.update_or_create(
                store=self.store,
                email=email,
                defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "total_processed": created + updated + skipped,
            "errors": errors[:50],
        }
