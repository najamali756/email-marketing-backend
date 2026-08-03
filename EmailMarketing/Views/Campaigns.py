from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.BusinessLogic.BulkEmailSender import BulkEmailSender
from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient, EmailCampaignStatusEnum
from EmailMarketing.Serializer.CampaignSerializer import (
    CampaignRecipientSerializer,
    EmailCampaignCreateSerializer,
    EmailCampaignSerializer,
    SendCampaignSerializer,
)
from EmailMarketing.Views.base import StoreAuthenticatedMixin


class EmailCampaignListCreateView(StoreAuthenticatedMixin, ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return EmailCampaignCreateSerializer
        return EmailCampaignSerializer

    def get_queryset(self):
        return EmailCampaign.objects.filter(store=self.request.store).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(store=self.request.store, status=EmailCampaignStatusEnum.draft.value)


class EmailCampaignDetailView(StoreAuthenticatedMixin, RetrieveUpdateAPIView):
    serializer_class = EmailCampaignSerializer

    def get_queryset(self):
        return EmailCampaign.objects.filter(store=self.request.store)


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
            BulkEmailSender(campaign.id).send_test_email(
                serializer.validated_data["test_email"],
                personalization=personalization
            )
            return Response({"detail": "Test email sent."})

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


class CampaignRecipientsView(StoreAuthenticatedMixin, APIView):
    def get(self, request, campaign_id):
        campaign = EmailCampaign.objects.filter(id=campaign_id, store=request.store).first()
        if not campaign:
            return Response({"detail": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)

        recipients = EmailCampaignRecipient.objects.filter(campaign=campaign).select_related("contact")[:100]
        return Response({
            "results": CampaignRecipientSerializer(recipients, many=True).data,
            "total_recipients": campaign.total_recipients,
        })


class UploadCampaignRecipientsView(StoreAuthenticatedMixin, APIView):
    def post(self, request, campaign_id):
        import csv
        import io
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        from EmailMarketing.BusinessLogic.TemplateRenderer import TemplateRenderer

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

        # Clear existing recipients for this campaign to replace them
        EmailCampaignRecipient.objects.filter(campaign=campaign).delete()

        created_count = 0
        from Accounts.models import Contact
        
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
