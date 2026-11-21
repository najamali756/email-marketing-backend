import secrets
from django.contrib.auth.hashers import identify_hasher
from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.db.models import Q
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from Accounts.models import Client, ClientUser, Store, User, UserRoleEnum
from Accounts.serializers import (
    ClientSerializer,
    StoreSerializer,
    UserSerializer,
    build_client_access_list,
)

SSO_TOKEN_SALT = "sso_external_temp_token"
TEMP_TOKEN_EXPIRY_SECONDS = 300  # 5 minutes


def set_user_password(user, password_val):
    """
    Supports both raw passwords and imported Django password hashes.
    If password_val is already a Django hash (e.g. pbkdf2_sha256$...),
    it assigns it directly without double-hashing.
    """
    if not password_val:
        return
    try:
        identify_hasher(password_val)
        # Valid Django hash format: assign directly to support hash import
        user.password = password_val
    except (ValueError, Exception):
        # Plain text password: hash via standard set_password
        user.set_password(password_val)


class ExternalUserCreateView(APIView):
    """
    API 1: External User Provisioning & Linking
    Creates a new user or grants an existing user access to the store's client.
    Supports importing existing Django password hashes and user details.
    
    Required payload:
      - external_token: The store's third_party_token
      - username (or email): Username / email for the user
      - password: Password (plain text or Django hash)
    Optional payload:
      - role: 'member' or 'admin' (default: 'member')
      - first_name, last_name
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        external_token = (
            request.data.get("external_token")
            or request.data.get("store_token")
            or request.data.get("third_party_token")
        )
        username = request.data.get("username") or request.data.get("email")
        password = request.data.get("password")
        role = request.data.get("role", UserRoleEnum.member.value)
        first_name = request.data.get("first_name", "")
        last_name = request.data.get("last_name", "")

        if not external_token:
            return Response(
                {
                    "status": "error",
                    "message": "external_token is required.",
                    "detail": "external_token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not username:
            return Response(
                {
                    "status": "error",
                    "message": "username or email is required.",
                    "detail": "username or email is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        username = str(username).strip()
        store = (
            Store.objects.filter(third_party_token=external_token, is_active=True)
            .select_related("client")
            .first()
        )
        if not store:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid or inactive store external token.",
                    "detail": "Invalid or inactive store external token.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        client = store.client
        if not client or not client.is_active:
            return Response(
                {
                    "status": "error",
                    "message": "Store is not associated with an active client.",
                    "detail": "Store is not associated with an active client.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(
            Q(email__iexact=username) | Q(username__iexact=username)
        ).first()

        assigned_role = (
            UserRoleEnum.admin.value if role == "admin" else UserRoleEnum.member.value
        )

        if user:
            # User already exists - ensure active access to store and client
            client_user, created_cu = ClientUser.objects.get_or_create(
                user=user,
                client=client,
                defaults={"role": assigned_role, "is_active": True},
            )
            if not created_cu and not client_user.is_active:
                client_user.is_active = True
                client_user.save(update_fields=["is_active", "updated_at"])

            user.assigned_stores.add(store)

            update_fields = []
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                update_fields.append("first_name")
            if last_name and user.last_name != last_name:
                user.last_name = last_name
                update_fields.append("last_name")

            if password:
                set_user_password(user, password)
                update_fields.append("password")

            if update_fields:
                update_fields.append("updated_at")
                user.save(update_fields=update_fields)

            return Response(
                {
                    "status": "success",
                    "message": "User access granted to store and client successfully.",
                },
                status=status.HTTP_200_OK,
            )

        # New user creation
        if not password:
            return Response(
                {
                    "status": "error",
                    "message": "password is required to create a new user.",
                    "detail": "password is required to create a new user.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User(
            email=username,
            username=username,
            first_name=first_name,
            last_name=last_name,
            user_type="operator",
        )
        set_user_password(user, password)
        user.save()

        ClientUser.objects.create(
            user=user,
            client=client,
            role=assigned_role,
            is_active=True,
        )
        user.assigned_stores.add(store)

        return Response(
            {
                "status": "success",
                "message": "User created and assigned to store successfully.",
            },
            status=status.HTTP_201_CREATED,
        )


class ExternalGenerateTempTokenView(APIView):
    """
    API 2: Temporary Token Generation
    Generates a cryptographically signed, short-lived single-use temporary token
    for SSO login redirect.
    
    Required payload:
      - external_token: The store's third_party_token
      - username (or email): Username / email of the user
    Optional payload:
      - password: User password (plain text or Django hash)
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        external_token = (
            request.data.get("external_token")
            or request.data.get("store_token")
            or request.data.get("third_party_token")
        )
        username = request.data.get("username") or request.data.get("email")
        password = request.data.get("password")

        if not external_token:
            return Response(
                {
                    "status": "error",
                    "message": "external_token is required.",
                    "detail": "external_token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not username:
            return Response(
                {
                    "status": "error",
                    "message": "username or email is required.",
                    "detail": "username or email is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        username = str(username).strip()
        store = (
            Store.objects.filter(third_party_token=external_token, is_active=True)
            .select_related("client")
            .first()
        )
        if not store:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid or inactive store external token.",
                    "detail": "Invalid or inactive store external token.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        user = User.objects.filter(
            Q(email__iexact=username) | Q(username__iexact=username),
            is_active=True,
        ).first()
        if not user:
            return Response(
                {
                    "status": "error",
                    "message": "User not found or inactive.",
                    "detail": "User not found or inactive.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if password:
            is_valid = False
            if password == user.password:
                is_valid = True
            elif user.check_password(password):
                is_valid = True

            if not is_valid:
                return Response(
                    {
                        "status": "error",
                        "message": "Invalid credentials for user.",
                        "detail": "Invalid credentials for user.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Verify user has access to store's client or is assigned to store
        has_client_access = ClientUser.objects.filter(
            user=user, client=store.client, is_active=True
        ).exists()
        has_store_access = (
            user.assigned_stores.filter(id=store.id).exists()
            or user.is_staff
            or user.is_superuser
        )

        if not (has_client_access or has_store_access):
            return Response(
                {
                    "status": "error",
                    "message": "User does not have access to this store.",
                    "detail": "User does not have access to this store.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        signer = TimestampSigner(salt=SSO_TOKEN_SALT)
        nonce = secrets.token_hex(16)
        payload = f"{user.id}:{store.id}:{nonce}"
        temp_token = signer.sign(payload)

        # Store nonce in cache to track active validity
        cache.set(
            f"sso_temp_active_{nonce}",
            {"user_id": user.id, "store_id": store.id},
            timeout=TEMP_TOKEN_EXPIRY_SECONDS,
        )

        return Response(
            {
                "status": "success",
                "temp_token": temp_token,
                "expires_in": TEMP_TOKEN_EXPIRY_SECONDS,
                "store_id": store.id,
                "store_name": store.name,
                "redirect_param": f"?token={temp_token}",
            },
            status=status.HTTP_200_OK,
        )


class ExternalExchangeTempTokenView(APIView):
    """
    API 3: Temporary Token Exchange for Real Session
    Exchanges the single-use temporary token for a real DRF authentication token
    and complete login session data.
    
    The temp token is strictly single-use and invalid for regular API authorization.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def _exchange(self, request):
        temp_token = (
            request.data.get("temp_token")
            or request.data.get("token")
            or request.query_params.get("token")
            or request.query_params.get("temp_token")
        )

        if not temp_token:
            return Response(
                {
                    "status": "error",
                    "message": "Temporary token is required.",
                    "detail": "Temporary token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        signer = TimestampSigner(salt=SSO_TOKEN_SALT)
        try:
            raw_payload = signer.unsign(temp_token, max_age=TEMP_TOKEN_EXPIRY_SECONDS)
        except SignatureExpired:
            return Response(
                {
                    "status": "error",
                    "message": "Temporary login token has expired. Please request a new one.",
                    "detail": "Temporary login token has expired. Please request a new one.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BadSignature:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid temporary login token.",
                    "detail": "Invalid temporary login token.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            parts = raw_payload.split(":")
            user_id = int(parts[0])
            store_id = int(parts[1])
            nonce = parts[2]
        except (IndexError, ValueError):
            return Response(
                {
                    "status": "error",
                    "message": "Malformed token structure.",
                    "detail": "Malformed token structure.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check and enforce single-use replay protection
        used_key = f"sso_token_used_{nonce}"
        if cache.get(used_key):
            return Response(
                {
                    "status": "error",
                    "message": "This temporary login token has already been used.",
                    "detail": "This temporary login token has already been used.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        cache.set(used_key, True, timeout=TEMP_TOKEN_EXPIRY_SECONDS)
        cache.delete(f"sso_temp_active_{nonce}")

        user = User.objects.filter(id=user_id, is_active=True).first()
        if not user:
            return Response(
                {
                    "status": "error",
                    "message": "User not found or account is deactivated.",
                    "detail": "User not found or account is deactivated.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        store = (
            Store.objects.filter(id=store_id, is_active=True)
            .select_related("client")
            .first()
        )

        auth_token, _ = Token.objects.get_or_create(user=user)
        user_type = "staff" if (user.is_staff or user.is_superuser) else user.user_type

        return Response(
            {
                "token": auth_token.key,
                "user": {
                    **UserSerializer(user).data,
                    "user_type": user_type,
                },
                "clients": build_client_access_list(user),
                "store_id": store.id if store else None,
                "store_name": store.name if store else None,
                "client_id": store.client.id if (store and store.client) else None,
                "client_name": store.client.name if (store and store.client) else None,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        return self._exchange(request)

    def get(self, request):
        return self._exchange(request)


class ExternalDashboardStatsView(APIView):
    """
    External API: Omnexa Marketing Dashboard & Contact Engagement Stats
    Called by Nexora CRM or other authorized services.
    Authenticated via:
      - Header 'X-Omnexa-Key' or 'X-API-Key'
      - Authorization: Bearer <key>
      - Query param: ?api_key=<key> or ?omnexa_key=<key>
    Filters:
      - start_date (ISO date: YYYY-MM-DD or datetime)
      - end_date (ISO date: YYYY-MM-DD or datetime)
      - email (optional customer email filter)
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def _resolve_store(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        key = (
            request.META.get("HTTP_X_OMNEXA_KEY")
            or request.META.get("HTTP_X_API_KEY")
            or request.GET.get("omnexa_key")
            or request.GET.get("api_key")
            or request.data.get("omnexa_key")
            or request.data.get("api_key")
            or request.data.get("external_token")
        )
        if not key and auth_header:
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() in ("bearer", "token", "key"):
                key = parts[1]

        if not key:
            return None, "Omnexa API key (X-Omnexa-Key or ?api_key=) is required."

        key = str(key).strip()
        store = (
            Store.objects.filter(Q(api_key=key) | Q(third_party_token=key), is_active=True)
            .select_related("client")
            .first()
        )
        if not store:
            return None, "Invalid or inactive Omnexa API key."

        return store, None

    def _handle_stats(self, request):
        from django.db.models import Sum, Count, Q
        from django.utils.dateparse import parse_date, parse_datetime
        from EmailMarketing.models import EmailCampaign, EmailCampaignRecipient

        store, err = self._resolve_store(request)
        if err:
            return Response({"status": "error", "message": err, "detail": err}, status=status.HTTP_401_UNAUTHORIZED)

        # Filters
        start_date_str = request.GET.get("start_date") or request.data.get("start_date")
        end_date_str = request.GET.get("end_date") or request.data.get("end_date")
        customer_email = request.GET.get("email") or request.data.get("email")

        campaigns_qs = EmailCampaign.objects.filter(store=store)

        if start_date_str:
            d = parse_date(start_date_str) or parse_datetime(start_date_str)
            if d:
                campaigns_qs = campaigns_qs.filter(Q(sent_at__gte=d) | Q(created_at__gte=d))

        if end_date_str:
            d = parse_date(end_date_str) or parse_datetime(end_date_str)
            if d:
                if hasattr(d, "date"):
                    campaigns_qs = campaigns_qs.filter(Q(sent_at__lte=d) | Q(created_at__lte=d))
                else:
                    from datetime import datetime, time
                    dt = datetime.combine(d, time.max)
                    campaigns_qs = campaigns_qs.filter(Q(sent_at__lte=dt) | Q(created_at__lte=dt))

        agg = campaigns_qs.aggregate(
            total_campaigns=Count("id"),
            total_recipients=Sum("total_recipients"),
            total_sent=Sum("sent_count"),
            total_opens=Sum("open_count"),
            total_clicks=Sum("click_count"),
            total_orders=Sum("orders_count"),
            total_revenue=Sum("revenue"),
        )

        total_campaigns = agg.get("total_campaigns") or 0
        total_recipients = agg.get("total_recipients") or 0
        total_sent = agg.get("total_sent") or 0
        total_opens = agg.get("total_opens") or 0
        total_clicks = agg.get("total_clicks") or 0
        total_orders = agg.get("total_orders") or 0
        total_revenue = float(agg.get("total_revenue") or 0.0)

        open_rate = round((total_opens / total_sent * 100), 1) if total_sent > 0 else 0.0
        click_rate = round((total_clicks / total_sent * 100), 1) if total_sent > 0 else 0.0
        conversion_rate = round((total_orders / total_sent * 100), 1) if total_sent > 0 else 0.0

        # Recent campaigns list (top 20)
        recent_campaigns = []
        for c in campaigns_qs.order_by("-sent_at", "-created_at")[:20]:
            sent_count = c.sent_count or 0
            open_count = c.open_count or 0
            click_count = c.click_count or 0
            orders_count = c.orders_count or 0
            rev = float(c.revenue or 0.0)
            c_open_rate = round((open_count / sent_count * 100), 1) if sent_count > 0 else 0.0
            c_click_rate = round((click_count / sent_count * 100), 1) if sent_count > 0 else 0.0

            recent_campaigns.append({
                "id": c.id,
                "name": c.name,
                "subject": c.subject,
                "status": c.status,
                "campaign_type": c.campaign_type,
                "sent_at": c.sent_at.isoformat() if c.sent_at else (c.created_at.isoformat() if c.created_at else None),
                "total_recipients": c.total_recipients or 0,
                "sent_count": sent_count,
                "open_count": open_count,
                "click_count": click_count,
                "orders_count": orders_count,
                "revenue": rev,
                "open_rate": c_open_rate,
                "click_rate": c_click_rate,
            })

        # Specific customer email marketing journey (if email filter is passed)
        customer_activity = None
        if customer_email and customer_email.strip():
            clean_email = customer_email.strip().lower()
            recipients_qs = EmailCampaignRecipient.objects.filter(
                campaign__store=store,
                email__iexact=clean_email
            ).select_related("campaign").order_by("-created_at")

            r_agg = recipients_qs.aggregate(
                total_received=Count("id"),
                total_opens=Count("opened_at"),
                total_clicks=Count("clicked_at"),
                total_orders=Count("converted_at"),
                lifetime_revenue=Sum("order_total")
            )

            rec_list = []
            for r in recipients_qs[:15]:
                rec_list.append({
                    "campaign_id": r.campaign_id,
                    "campaign_name": r.campaign.name if r.campaign else "Unknown Campaign",
                    "subject": r.campaign.subject if r.campaign else "",
                    "status": r.status,
                    "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                    "opened_at": r.opened_at.isoformat() if r.opened_at else None,
                    "clicked_at": r.clicked_at.isoformat() if r.clicked_at else None,
                    "converted_at": r.converted_at.isoformat() if r.converted_at else None,
                    "order_total": float(r.order_total or 0.0),
                })

            customer_activity = {
                "email": clean_email,
                "total_emails_received": r_agg.get("total_received") or 0,
                "total_opens": r_agg.get("total_opens") or 0,
                "total_clicks": r_agg.get("total_clicks") or 0,
                "total_conversions": r_agg.get("total_orders") or 0,
                "lifetime_email_revenue": float(r_agg.get("lifetime_revenue") or 0.0),
                "history": rec_list
            }

        return Response({
            "status": "success",
            "store": {
                "id": store.id,
                "name": store.name,
                "shop_url": store.shop_url,
                "currency": store.store_currency or "PKR",
            },
            "summary": {
                "total_campaigns": total_campaigns,
                "total_recipients": total_recipients,
                "total_sent": total_sent,
                "total_opens": total_opens,
                "total_clicks": total_clicks,
                "total_orders": total_orders,
                "total_revenue": total_revenue,
                "open_rate": open_rate,
                "click_rate": click_rate,
                "conversion_rate": conversion_rate,
            },
            "campaigns": recent_campaigns,
            "customer_activity": customer_activity
        }, status=status.HTTP_200_OK)

    def get(self, request):
        return self._handle_stats(request)

    def post(self, request):
        return self._handle_stats(request)
