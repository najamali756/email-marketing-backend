from django.core.cache import cache
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
import logging
from EmailMarketing.BusinessLogic.BulkEmailSender import BulkEmailSender
from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient, EmailCampaignStatusEnum
from EmailMarketing.Serializer.CampaignSerializer import (
    CampaignRecipientSerializer,
    EmailCampaignCreateSerializer,
    EmailCampaignSerializer,
    EmailCampaignListSerializer,
    SendCampaignSerializer,
)
from EmailMarketing.Views.base import StoreAuthenticatedMixin
import csv
import io
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from EmailMarketing.BusinessLogic.TemplateRenderer import TemplateRenderer

from django.db.models import Case, Count, IntegerField, Q, Sum, When
from EmailMarketing.models import EmailRecipientStatusEnum
from Accounts.models import Contact
from shopify_integration.sync import fetch_and_save_store_currency

logger = logging.getLogger(__name__)
def get_campaign_cache_version(store_id):
    try:
        return cache.get(f"camp_ver_{store_id}", 1)
    except Exception:
        return 1

def invalidate_campaign_cache(store_id):
    try:
        cache.incr(f"camp_ver_{store_id}")
    except Exception:
        try:
            cache.set(f"camp_ver_{store_id}", 2)
        except Exception:
            pass

class EmailCampaignListCreateView(StoreAuthenticatedMixin, ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return EmailCampaignCreateSerializer
        return EmailCampaignListSerializer

    def get_queryset(self):
        return EmailCampaign.objects.filter(store=self.request.store).select_related("segment").order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(store=self.request.store, status=EmailCampaignStatusEnum.draft.value)
        invalidate_campaign_cache(self.request.store.id)

    def get(self, request, *args, **kwargs):
        store_id = request.META.get("HTTP_X_STORE_ID") or request.GET.get("store_id")
        if not store_id:
            return Response(
                {"detail": "Store ID is required in headers (X-Store-Id) or query parameters.", "count": 0, "total_pages": 1, "current_page": 1, "page_size": 10, "results": []},
                status=status.HTTP_400_BAD_REQUEST
            )

        store = request.store
        if not store:
            return Response(
                {"detail": "Store context not found or access denied.", "count": 0, "total_pages": 1, "current_page": 1, "page_size": 10, "results": []},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            page = int(request.GET.get("page", 1))
        except (ValueError, TypeError):
            page = 1

        try:
            page_size = int(request.GET.get("page_size", 10))
        except (ValueError, TypeError):
            page_size = 10

        search = request.GET.get("search", "").strip()
        status_param = request.GET.get("status", "").strip()
        type_param = request.GET.get("campaign_type", "").strip() or request.GET.get("type", "").strip()

        ver = get_campaign_cache_version(store.id)
        cache_key = f"camp_list_response_v2_{store.id}_v{ver}_{page}_{page_size}_{search}_{status_param}_{type_param}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        qs = EmailCampaign.objects.filter(store=store).select_related("segment").only(
            "id", "name", "subject", "preview_text", "status", "campaign_type",
            "scheduled_at", "sent_at", "total_recipients", "sent_count",
            "failed_count", "skipped_count", "open_count", "click_count",
            "revenue", "orders_count", "wizard_step", "created_at", "updated_at",
            "segment__name", "segment__id"
        ).order_by("-created_at")

        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(subject__icontains=search))

        if status_param and status_param.lower() != "all":
            qs = qs.filter(status__iexact=status_param)

        if type_param and type_param.lower() != "all":
            qs = qs.filter(campaign_type__iexact=type_param)

        total_count = qs.count()
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
        if page < 1:
            page = 1

        offset = (page - 1) * page_size
        page_items = list(qs[offset:offset + page_size])

        serializer = EmailCampaignListSerializer(page_items, many=True)
        response_data = {
            "count": total_count,
            "total_pages": total_pages,
            "current_page": page,
            "page_size": page_size,
            "currency": getattr(store, "store_currency", None) or "PKR",
            "store_currency": getattr(store, "store_currency", None) or "PKR",
            "results": serializer.data,
        }

        cache.set(cache_key, response_data, 30)
        return Response(response_data)


class EmailCampaignStatsView(StoreAuthenticatedMixin, APIView):
    def get(self, request):
        store_id = request.META.get("HTTP_X_STORE_ID") or request.GET.get("store_id")
        if not store_id:
            return Response(
                {"detail": "Store ID is required in headers (X-Store-Id) or query parameters.", "total": 0, "sent": 0, "scheduled": 0, "drafts": 0, "stats": {}},
                status=status.HTTP_400_BAD_REQUEST
            )

        store = request.store
        if not store:
            return Response(
                {"detail": "Store context not found or access denied.", "total": 0, "sent": 0, "scheduled": 0, "drafts": 0, "stats": {}},
                status=status.HTTP_403_FORBIDDEN
            )

        ver = get_campaign_cache_version(store.id)
        cache_key = f"camp_stats_{store.id}_v{ver}"
        cached_stats = cache.get(cache_key)
        if cached_stats is not None:
            return Response(cached_stats)

        base_qs = EmailCampaign.objects.filter(store=store)
        agg = base_qs.aggregate(
            total=Count("id"),
            sent=Count(Case(When(status__in=[EmailCampaignStatusEnum.sent.value, EmailCampaignStatusEnum.sending.value, "Sent", "Sending"], then=1), output_field=IntegerField())),
            scheduled=Count(Case(When(status__in=[EmailCampaignStatusEnum.scheduled.value, "Scheduled"], then=1), output_field=IntegerField())),
            drafts=Count(Case(When(status__in=[EmailCampaignStatusEnum.draft.value, "Draft"], then=1), output_field=IntegerField())),
            cancelled=Count(Case(When(status__in=[EmailCampaignStatusEnum.cancelled.value, "Cancelled"], then=1), output_field=IntegerField())),
            failed=Count(Case(When(status__in=[EmailCampaignStatusEnum.failed.value, "Failed"], then=1), output_field=IntegerField())),
            total_revenue=Sum("revenue"),
            total_sent=Sum("sent_count"),
            total_opened=Sum("open_count"),
            total_clicked=Sum("click_count"),
        )
       
        store_currency = fetch_and_save_store_currency(store)

        stats_payload = {
            "total": agg["total"] or 0,
            "sent": agg["sent"] or 0,
            "scheduled": agg["scheduled"] or 0,
            "drafts": agg["drafts"] or 0,
            "cancelled": agg["cancelled"] or 0,
            "failed": agg["failed"] or 0,
            "total_revenue": float(agg["total_revenue"] or 0),
            "total_sent": agg["total_sent"] or 0,
            "total_opened": agg["total_opened"] or 0,
            "total_clicked": agg["total_clicked"] or 0,
            "currency": store_currency,
            "store_currency": store_currency,
        }
        res_data = dict(stats_payload)
        res_data["currency"] = store_currency
        res_data["store_currency"] = store_currency
        res_data["stats"] = stats_payload
        cache.set(cache_key, res_data, 60)
        return Response(res_data)


def recalculate_campaign_stats(campaign):
    if not campaign:
        return campaign
    recipients = campaign.recipients.all()
    campaign.sent_count = recipients.filter(sent_at__isnull=False).count()
    campaign.failed_count = recipients.filter(status=EmailRecipientStatusEnum.failed.value).count()
    campaign.skipped_count = recipients.filter(status=EmailRecipientStatusEnum.skipped.value).count()
    campaign.open_count = recipients.filter(
        Q(opened_at__isnull=False) |
        Q(status__in=[
            EmailRecipientStatusEnum.opened.value,
            EmailRecipientStatusEnum.clicked.value,
            EmailRecipientStatusEnum.added_to_cart.value,
            EmailRecipientStatusEnum.checkout_started.value,
            EmailRecipientStatusEnum.purchased.value
        ])
    ).distinct().count()
    
    campaign.click_count = recipients.filter(
        Q(clicked_at__isnull=False) |
        Q(status__in=[
            EmailRecipientStatusEnum.clicked.value,
            EmailRecipientStatusEnum.added_to_cart.value,
            EmailRecipientStatusEnum.checkout_started.value,
            EmailRecipientStatusEnum.purchased.value
        ])
    ).distinct().count()
    
    campaign.unsubscribe_count = recipients.filter(unsubscribed_at__isnull=False).count()
    campaign.orders_count = recipients.filter(
        Q(converted_at__isnull=False) |
        Q(status=EmailRecipientStatusEnum.purchased.value)
    ).distinct().count()

    campaign.page_view_count = recipients.aggregate(total_pv=Sum("page_view_count"))["total_pv"] or 0
    campaign.add_to_cart_count = recipients.aggregate(total_cart=Sum("add_to_cart_count"))["total_cart"] or 0
    campaign.checkout_started_count = recipients.aggregate(total_co=Sum("checkout_started_count"))["total_co"] or 0

    rev_sum = recipients.aggregate(total_rev=Sum("order_total"))["total_rev"] or 0
    campaign.revenue = rev_sum
    campaign.save(update_fields=[
        "sent_count", "failed_count", "skipped_count", "open_count",
        "click_count", "page_view_count", "add_to_cart_count", "checkout_started_count",
        "unsubscribe_count", "orders_count", "revenue", "updated_at"
    ])
    return campaign


class EmailCampaignDetailView(StoreAuthenticatedMixin, RetrieveUpdateDestroyAPIView):
    serializer_class = EmailCampaignSerializer

    def get_queryset(self):
        return EmailCampaign.objects.filter(store=self.request.store)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        recalculate_campaign_stats(instance)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        status_val = str(instance.status or '').strip().lower()
        if status_val in ['sent', 'sending']:
            return Response(
                {'detail': 'Cannot delete a campaign that is already sent or currently sending.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        store_id = instance.store_id
        self.perform_destroy(instance)
        invalidate_campaign_cache(store_id)
        return Response(
            {'detail': 'Campaign deleted successfully.'},
            status=status.HTTP_200_OK,
        )


class RecalculateCampaignStatsView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        recalculate_campaign_stats(campaign)
        serializer = EmailCampaignSerializer(campaign)
        return Response(serializer.data)


class BuildCampaignAudienceView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        segment_ids = request.data.get("segment_ids", [])
        target_all_contacts = request.data.get("target_all_contacts", False)
        specific_emails = request.data.get("specific_emails", [])

        if segment_ids:
            campaign.segment_id = segment_ids[0]
            campaign.save(update_fields=["segment_id", "updated_at"])

        total = BulkEmailSender(campaign.id).build_recipients(
            segment_ids=segment_ids,
            target_all_contacts=target_all_contacts,
            specific_emails=specific_emails
        )

        return Response({
            "campaign_id": campaign.id,
            "total_recipients": total,
        })


class SendCampaignView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SendCampaignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if serializer.validated_data.get("test_email"):
            personalization = serializer.validated_data.get("personalization", {})
            try:
                BulkEmailSender(campaign.id).send_test_email(
                    serializer.validated_data["test_email"],
                    personalization=personalization
                )
                return Response({"detail": "Test email sent."})
            except Exception as e:
                logger.error(f"[SEND TEST EMAIL ERROR] Campaign {campaign_id}: {str(e)}", exc_info=True)
                return Response({"detail": f"Failed to send test email: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        if campaign.total_recipients == 0:
            total = BulkEmailSender(campaign.id).build_recipients()
            if total == 0:
                return Response({"detail": "No recipients found."}, status=400)

        if campaign.status == EmailCampaignStatusEnum.sending.value:
            return Response({"detail": "Campaign is already sending."}, status=400)

        BulkEmailSender(campaign.id).send_async()
        return Response({
            "detail": "Campaign send started.",
            "campaign_id": campaign.id,
            "status": EmailCampaignStatusEnum.sending.value,
        })


class PauseCampaignView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        if campaign.status not in [EmailCampaignStatusEnum.sending.value, "Sending"]:
            return Response({"detail": f"Cannot pause a campaign with status '{campaign.status}'."}, status=400)

        campaign.status = "Paused"
        campaign.save(update_fields=["status", "updated_at"])
        return Response({
            "detail": "Campaign paused successfully.",
            "campaign_id": campaign.id,
            "status": "Paused",
        })


class ResumeCampaignView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        if campaign.status in [EmailCampaignStatusEnum.sent.value, "Sent"]:
            return Response({"detail": "Campaign has already completed."}, status=400)

        campaign.status = EmailCampaignStatusEnum.sending.value
        campaign.save(update_fields=["status", "updated_at"])

        BulkEmailSender(campaign.id).send_async()
        return Response({
            "detail": "Campaign send resumed successfully.",
            "campaign_id": campaign.id,
            "status": EmailCampaignStatusEnum.sending.value,
        })


class CancelCampaignView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        campaign.status = EmailCampaignStatusEnum.cancelled.value
        campaign.save(update_fields=["status", "updated_at"])
        return Response({
            "detail": "Campaign stopped / cancelled.",
            "campaign_id": campaign.id,
            "status": EmailCampaignStatusEnum.cancelled.value,
        })


class CampaignRecipientsView(StoreAuthenticatedMixin, APIView):
    def get(self, request, campaign_id):
        campaign = None
        if hasattr(request, "store") and request.store:
            campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).only("id", "total_recipients").first()
        if not campaign:
            campaign = EmailCampaign.objects.filter(id=campaign_id).only("id", "total_recipients").first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        base_qs = EmailCampaignRecipient.objects.filter(campaign=campaign)

        # 1. Single optimized SQL aggregation query for all status breakdown counts
        status_counts_agg = base_qs.aggregate(
            all_count=Count("id"),
            purchased=Count("id", filter=Q(status=EmailRecipientStatusEnum.purchased.value) | Q(converted_at__isnull=False) | Q(order_total__gt=0)),
            checkout_started=Count("id", filter=Q(status=EmailRecipientStatusEnum.checkout_started.value) | Q(checkout_started_count__gt=0)),
            added_to_cart=Count("id", filter=Q(status=EmailRecipientStatusEnum.added_to_cart.value) | Q(add_to_cart_count__gt=0)),
            clicked=Count("id", filter=Q(status=EmailRecipientStatusEnum.clicked.value) | Q(clicked_at__isnull=False)),
            opened=Count("id", filter=Q(status=EmailRecipientStatusEnum.opened.value) | Q(opened_at__isnull=False)),
            sent=Count("id", filter=Q(status=EmailRecipientStatusEnum.sent.value)),
            failed=Count("id", filter=Q(status=EmailRecipientStatusEnum.failed.value) | Q(error_message__isnull=False)),
            unsubscribed=Count("id", filter=Q(status=EmailRecipientStatusEnum.unsubscribed.value) | Q(unsubscribed_at__isnull=False)),
        )

        status_counts = {
            "all": campaign.total_recipients or status_counts_agg["all_count"] or 0,
            "purchased": status_counts_agg["purchased"] or 0,
            "checkout_started": status_counts_agg["checkout_started"] or 0,
            "added_to_cart": status_counts_agg["added_to_cart"] or 0,
            "clicked": status_counts_agg["clicked"] or 0,
            "opened": status_counts_agg["opened"] or 0,
            "sent": status_counts_agg["sent"] or 0,
            "failed": status_counts_agg["failed"] or 0,
            "unsubscribed": status_counts_agg["unsubscribed"] or 0,
        }

        # Optimized select_related and only required columns
        qs = base_qs.select_related("contact").only(
            "id", "email", "status", "sent_at", "opened_at", "clicked_at", "unsubscribed_at", "converted_at",
            "page_view_count", "add_to_cart_count", "checkout_started_count", "cart_total",
            "order_id", "order_total", "discount_code", "error_message",
            "contact__first_name", "contact__last_name"
        )

        # 2. Server-Side Status Filter
        status_filter = (request.query_params.get("status") or "all").strip().lower()
        normalized_filter_key = "all"
        if status_filter in ["purchased", "converted"]:
            qs = qs.filter(Q(status=EmailRecipientStatusEnum.purchased.value) | Q(converted_at__isnull=False) | Q(order_total__gt=0))
            normalized_filter_key = "purchased"
        elif status_filter in ["checkout_started", "checkout", "checkoutstarted"]:
            qs = qs.filter(Q(status=EmailRecipientStatusEnum.checkout_started.value) | Q(checkout_started_count__gt=0))
            normalized_filter_key = "checkout_started"
        elif status_filter in ["added_to_cart", "cart", "addedtocart"]:
            qs = qs.filter(Q(status=EmailRecipientStatusEnum.added_to_cart.value) | Q(add_to_cart_count__gt=0))
            normalized_filter_key = "added_to_cart"
        elif status_filter in ["clicked"]:
            qs = qs.filter(Q(status=EmailRecipientStatusEnum.clicked.value) | Q(clicked_at__isnull=False))
            normalized_filter_key = "clicked"
        elif status_filter in ["opened"]:
            qs = qs.filter(Q(status=EmailRecipientStatusEnum.opened.value) | Q(opened_at__isnull=False))
            normalized_filter_key = "opened"
        elif status_filter in ["sent"]:
            qs = qs.filter(status=EmailRecipientStatusEnum.sent.value)
            normalized_filter_key = "sent"
        elif status_filter in ["failed", "bounced"]:
            qs = qs.filter(Q(status=EmailRecipientStatusEnum.failed.value) | Q(error_message__isnull=False))
            normalized_filter_key = "failed"
        elif status_filter in ["unsubscribed"]:
            qs = qs.filter(Q(status=EmailRecipientStatusEnum.unsubscribed.value) | Q(unsubscribed_at__isnull=False))
            normalized_filter_key = "unsubscribed"

        # 3. Server-Side Search Filter
        search_query = request.query_params.get("search", "").strip()
        if search_query:
            qs = qs.filter(
                Q(email__icontains=search_query) |
                Q(order_id__icontains=search_query) |
                Q(discount_code__icontains=search_query) |
                Q(contact__first_name__icontains=search_query) |
                Q(contact__last_name__icontains=search_query)
            )
            total_filtered_count = qs.count()
        else:
            # Re-use pre-calculated count without extra database count query
            total_filtered_count = status_counts.get(normalized_filter_key, status_counts["all"])

        # 4. Ordering
        ordering = request.query_params.get("ordering")
        if ordering:
            qs = qs.order_by(ordering)
        else:
            # Default ordering: Prioritize engaged recipients (purchased, clicked, opened) first
            qs = qs.order_by("-converted_at", "-order_total", "-checkout_started_count", "-add_to_cart_count", "-opened_at", "-id")

        # 5. Pagination
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (ValueError, TypeError):
            page = 1

        try:
            page_size = min(200, max(10, int(request.query_params.get("page_size", 50))))
        except (ValueError, TypeError):
            page_size = 50

        offset = (page - 1) * page_size
        paginated_recipients = qs[offset : offset + page_size]
        total_pages = (total_filtered_count + page_size - 1) // page_size if total_filtered_count > 0 else 1

        return Response({
            "results": CampaignRecipientSerializer(paginated_recipients, many=True).data,
            "total_recipients": campaign.total_recipients,
            "total_filtered": total_filtered_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_counts": status_counts,
        })


class UploadCampaignRecipientsView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        

        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        csv_file = request.FILES.get("file")
        if not csv_file:
            return Response(
                {"detail": "No file uploaded. Please send a 'file' parameter with your CSV."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            decoded_file = csv_file.read().decode("utf-8")
            csv_data = io.StringIO(decoded_file)
            reader = csv.DictReader(csv_data)
            headers = [h.strip() for h in reader.fieldnames] if reader.fieldnames else []
        except Exception as e:
            return Response({"detail": f"Failed to parse CSV file: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        normalized_headers = {h.lower(): h for h in headers}

        email_header_orig = None
        for key in ["email", "e-mail", "email_address", "email address"]:
            if key in normalized_headers:
                email_header_orig = normalized_headers[key]
                break

        if not email_header_orig:
            for h in headers:
                if "email" in h.lower():
                    email_header_orig = h
                    break

        if not email_header_orig:
            return Response(
                {"detail": "CSV must contain an 'email' column (e.g. 'email', 'Email', etc.)."},
                status=status.HTTP_400_BAD_REQUEST
            )

        html_content = campaign.html_content or (campaign.template.html_content if campaign.template else "")
        subject = campaign.subject or ""
        preview_text = campaign.preview_text or ""
        template_variables = (
            set(TemplateRenderer.extract_variables(html_content)) |
            set(TemplateRenderer.extract_variables(subject)) |
            set(TemplateRenderer.extract_variables(preview_text))
        )

        missing_columns = []
        for var in template_variables:
            if var.lower() not in normalized_headers:
                missing_columns.append(var)

        seen_emails = set()
        duplicates_removed = 0
        valid_recipients = []
        
        try:
            rows = list(reader)
        except Exception as e:
            return Response({"detail": f"Failed reading CSV rows: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        for row in rows:
            email_val = row.get(email_header_orig)
            if not email_val:
                continue
            email_val = email_val.strip()
            
            try:
                validate_email(email_val)
            except ValidationError:
                continue
                
            email_lower = email_val.lower()
            if email_lower in seen_emails:
                duplicates_removed += 1
                continue
            
            seen_emails.add(email_lower)
            valid_recipients.append((email_val, row))

        EmailCampaignRecipient.objects.filter(campaign=campaign).delete()

        created_count = 0
       
        
        for email_val, row in valid_recipients:
            contact, created = Contact.objects.get_or_create(
                store=request.store,
                email=email_val.lower()
            )
            updated_fields = []
            
            def get_row_value(field_names):
                for fn in field_names:
                    orig_h = normalized_headers.get(fn)
                    if orig_h:
                        return row.get(orig_h)
                return None

            first_name = get_row_value(["first_name", "first name", "name", "fname"])
            if first_name:
                contact.first_name = first_name.strip()
                updated_fields.append("first_name")

            last_name = get_row_value(["last_name", "last name", "lname"])
            if last_name:
                contact.last_name = last_name.strip()
                updated_fields.append("last_name")

            phone = get_row_value(["phone", "phone_number", "phone number", "tel"])
            if phone:
                contact.phone = phone.strip()
                updated_fields.append("phone")

            city = get_row_value(["city", "town"])
            if city:
                contact.city = city.strip()
                updated_fields.append("city")

            country = get_row_value(["country", "nation"])
            if country:
                contact.country = country.strip()
                updated_fields.append("country")

            contact.accept_email_marketing = True
            updated_fields.append("accept_email_marketing")
            
            if updated_fields:
                contact.save(update_fields=updated_fields)

            personalization = {}
            for h_orig in headers:
                h_clean = h_orig.strip().lower()
                val = row.get(h_orig)
                if val:
                    personalization[h_clean] = val.strip()

            personalization.update({
                "first_name": contact.first_name or "",
                "last_name": contact.last_name or "",
                "email": contact.email,
                "city": contact.city or "",
                "country": contact.country or "",
                "phone": contact.phone or "",
            })

            EmailCampaignRecipient.objects.create(
                campaign=campaign,
                contact=contact,
                email=contact.email,
                status="Pending",
                personalization=personalization
            )
            created_count += 1

        campaign.total_recipients = created_count
        campaign.save(update_fields=["total_recipients", "updated_at"])

        warning_msg = ""
        if missing_columns:
            warning_msg = f" Note: Missing template variables columns in CSV: {', '.join(missing_columns)}"

        return Response({
            "success": True,
            "total_uploaded": created_count,
            "duplicates_removed": duplicates_removed,
            "missing_variables": missing_columns,
            "message": f"Successfully validated and uploaded {created_count} recipients.{' Removed ' + str(duplicates_removed) + ' duplicate emails.' if duplicates_removed else ''}{warning_msg}"
        })
