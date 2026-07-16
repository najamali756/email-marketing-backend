from django.conf import settings
from django.utils.text import slugify
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from EmailMarketing.models import StoreSenderIdentity
from EmailMarketing.Serializer.TemplateSerializer import StoreSenderIdentitySerializer
from EmailMarketing.Views.base import StoreAuthenticatedMixin
from EmailMarketing.BusinessLogic.SESService import SESService


class StoreSenderIdentityView(StoreAuthenticatedMixin, APIView):
    def get(self, request):
        identity = StoreSenderIdentity.objects.filter(store=request.store, is_active=True).first()
        if not identity:
            return Response({"detail": "No sender identity found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(StoreSenderIdentitySerializer(identity).data)

    def post(self, request):
        mode = request.data.get("mode", "platform_domain")
        brand_name = request.data.get("brand_name", "")
        reply_to_email = request.data.get("reply_to_email", "")
        domain = request.data.get("domain", "")
        from_email = request.data.get("from_email", "")

        if not brand_name or not reply_to_email:
            return Response({"error": "Brand name and reply-to email are required."}, status=status.HTTP_400_BAD_REQUEST)

        # Get or create identity config
        identity, created = StoreSenderIdentity.objects.get_or_create(
            store=request.store,
            is_active=True,
            defaults={
                "mode": mode,
                "brand_name": brand_name,
                "reply_to_email": reply_to_email,
                "from_email": from_email,
                "domain": domain,
                "status": "pending"
            }
        )

        if not created:
            identity.mode = mode
            identity.brand_name = brand_name
            identity.reply_to_email = reply_to_email

        # Synchronize Store model default fallbacks behind the scenes
        request.store.default_from_name = brand_name
        request.store.default_from_email = reply_to_email
        request.store.save()

        platform_domain = getattr(settings, "SENDGRID_PLATFORM_DOMAIN", "ntechgreenbridge.com")

        if mode == "platform_domain":
            brand_slug = slugify(brand_name)
            identity.domain = platform_domain
            identity.from_email = f"{brand_slug}@{platform_domain}"
            identity.status = "verified"
            identity.sendgrid_domain_id = None
            identity.dns_records = None
            identity.save()
        else: # custom_domain
            if not domain or not from_email:
                return Response({"error": "Domain and From email are required for custom domain mode."}, status=status.HTTP_400_BAD_REQUEST)
            
            # If domain has changed or we didn't register it yet, call SES to register
            if identity.domain != domain or not identity.sendgrid_domain_id:
                try:
                    res = SESService.register_domain(domain)
                    identity.domain = domain
                    identity.sendgrid_domain_id = res.get("id")
                    identity.dns_records = res.get("dns")
                    identity.status = "pending"
                except Exception as e:
                    return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            identity.from_email = from_email
            identity.save()

        return Response(StoreSenderIdentitySerializer(identity).data)


class ValidateSenderIdentityView(StoreAuthenticatedMixin, APIView):
    def post(self, request):
        identity = StoreSenderIdentity.objects.filter(store=request.store, is_active=True).first()
        if not identity:
            return Response({"error": "No sender identity configuration found."}, status=status.HTTP_404_NOT_FOUND)

        if identity.mode == "platform_domain":
            identity.status = "verified"
            identity.save()
            return Response({
                "status": identity.status,
                "verified": True
            })

        if not identity.sendgrid_domain_id:
            return Response({"error": "Domain has not been registered with SES yet."}, status=status.HTTP_400_BAD_REQUEST)

        is_valid = SESService.validate_domain(identity.sendgrid_domain_id)
        if is_valid:
            identity.status = "verified"
        else:
            identity.status = "failed"
        identity.save()

        return Response({
            "status": identity.status,
            "verified": is_valid
        })
