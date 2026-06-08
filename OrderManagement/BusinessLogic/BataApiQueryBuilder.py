from datetime import datetime

from django.db.models import Q

from OrderManagement.models import BataApi, OrderStatus
from SaleChannel.models import SaleChannel


BATA_API_ONLY_FIELDS = (
    "id",
    "sale_json",
    "adjust_json",
    "return_json",
    "adjust_return_json",
    "source_location",
    "destination_location",
    "is_sale_adjusted",
    "is_fulfilled",
    "is_delivered",
    "is_return_posted",
    "child_orders_id",
    "child_orders__id",
    "child_orders__status",
    "child_orders__delivered_at",
    "child_orders__sale_channel_id",
)


def _parse_order_names(order_names_raw):
    if not order_names_raw:
        return []
    if isinstance(order_names_raw, str):
        return [name.strip() for name in order_names_raw.split(",") if name.strip()]
    return list(order_names_raw)


def _parse_dates(start_date_raw, end_date_raw):
    try:
        start_date = datetime.strptime(start_date_raw, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_raw, "%Y-%m-%d")
        return start_date, end_date
    except (TypeError, ValueError):
        return None, None


def _base_filters(*, is_home, is_iss_order, sale_channel, order_names, start_date, end_date):
    filters = {
        "child_orders__sale_channel": sale_channel,
    }
    if is_home:
        filters["is_home"] = True
    if is_iss_order:
        filters["is_iss_order"] = True

    if order_names:
        filters["child_orders__name__in"] = order_names
    if start_date and end_date:
        filters["child_orders__created_at__range"] = (start_date, end_date)
    return filters


def _finalize_queryset(queryset, sort):
    order_by = "-id" if sort == "desc" else "id"
    return (
        queryset.select_related("child_orders", "child_orders__sale_channel")
        .only(*BATA_API_ONLY_FIELDS)
        .order_by(order_by)
    )


def _dispatched_union_queryset(base_filters, pos_id):
    """
    Split destination/source OR into UNION so MySQL can use per-branch indexes.
    """
    status_q = Q(
        child_orders__status__in=[
            OrderStatus.dispatched.value,
            OrderStatus.delivered.value,
            OrderStatus.returned.value,
        ],
        sale_json__isnull=False,
    )
    dest_ids = (
        BataApi.objects.using("reader_host")
        .filter(
            status_q,
            destination_location=pos_id,
            is_fulfilled=False,
            **base_filters,
        )
        .values("pk")
    )
    source_ids = (
        BataApi.objects.using("reader_host")
        .filter(
            status_q,
            source_location=pos_id,
            is_sale_adjusted=False,
            **base_filters,
        )
        .values("pk")
    )
    return BataApi.objects.using("reader_host").filter(pk__in=dest_ids.union(source_ids))


def build_bata_api_queryset(request, *, is_home=False, is_iss_order=False):
    vendor = request.GET.get("vendor")
    status = request.GET.get("status")
    pos_id = request.GET.get("pos_id")
    sort = request.GET.get("sort")
    order_names = _parse_order_names(request.GET.get("order_names", ""))
    start_date, end_date = _parse_dates(
        request.GET.get("start_date"),
        request.GET.get("end_date"),
    )

    if vendor is None:
        return "please send vendor and is_sale"
    if status is None:
        return "status is mandatory"
    if pos_id is None:
        return "please send pos_id"

    pos_id = str(pos_id).strip()
    sale_channel = SaleChannel.objects.using("reader_host").filter(url=vendor).only("id").first()
    base_filters = _base_filters(
        is_home=is_home,
        is_iss_order=is_iss_order,
        sale_channel=sale_channel,
        order_names=order_names,
        start_date=start_date,
        end_date=end_date,
    )

    if status == OrderStatus.dispatched.value:
        queryset = _dispatched_union_queryset(base_filters, pos_id)
    elif status == OrderStatus.delivered.value:
        combined_query = (
            Q(
                child_orders__status=OrderStatus.delivered.value,
                is_delivered=False,
                is_sale_adjusted=True,
                sale_json__isnull=False,
            )
            | Q(
                child_orders__status=OrderStatus.returned.value,
                is_delivered=False,
                is_sale_adjusted=True,
                sale_json__isnull=False,
                child_orders__delivered_at__isnull=False,
            )
        )
        location_q = Q(source_location=pos_id)
        queryset = BataApi.objects.using("reader_host").filter(combined_query & location_q, **base_filters)
    elif status == OrderStatus.returned.value:
        combined_query = Q(
            child_orders__status__in=[OrderStatus.returned.value],
            return_json__isnull=False,
        )
        delivered_q = Q(child_orders__delivered_at__isnull=True) | Q(
            child_orders__delivered_at__isnull=False,
            is_delivered=True,
        )
        location_q = Q(source_location=pos_id, is_return_posted=False, is_sale_adjusted=True)
        queryset = BataApi.objects.using("reader_host").filter(
            combined_query & delivered_q & location_q,
            **base_filters,
        )
    else:
        return "status is not valid"

    return _finalize_queryset(queryset, sort)
