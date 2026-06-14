import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'email_marketing_ms_django_backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from Accounts.models import Client, ClientUser, UserRoleEnum
from Stores.models import Store
from EmailMarketing.models import EmailSegment
from Stores.BusinessLogic.CsvContactImporter import CsvContactImporter

User = get_user_model()

def seed():
    print("Seeding database...")
    # Create or get client
    client, created = Client.objects.get_or_create(
        name="MailFlow Demo Client",
        defaults={"is_active": True}
    )
    if created:
        print(f"Created Client: {client.name}")
    else:
        print(f"Found existing Client: {client.name}")

    # Create or get user
    user_email = "admin@example.com"
    user = User.objects.filter(email=user_email).first()
    if not user:
        user = User.objects.create_user(
            email=user_email,
            password="admin123",
            first_name="Admin",
            last_name="User"
        )
        print(f"Created User: {user.email} with password admin123")
    else:
        user.set_password("admin123")
        user.save()
        print(f"Found existing User: {user.email}. Password reset to admin123")

    # ClientUser linkage
    cu, created = ClientUser.objects.get_or_create(
        user=user,
        client=client,
        defaults={"role": UserRoleEnum.admin.value, "is_active": True}
    )
    if created:
        print(f"Linked user to client as admin")

    # Get Token
    token, _ = Token.objects.get_or_create(user=user)
    print(f"Token key: {token.key}")

    # Create Store
    store, created = Store.objects.get_or_create(
        client=client,
        shop_url="my-store.myshopify.com",
        defaults={
            "name": "My Awesome Store",
            "is_active": True,
            "email_provider": "smtp",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_username": "demo@gmail.com",
            "smtp_password": "demopassword",
            "smtp_use_tls": True,
            "default_from_email": "demo@gmail.com",
            "default_from_name": "Demo Admin"
        }
    )
    if created:
        print(f"Created Store: {store.name} (my-store.myshopify.com)")
    else:
        print(f"Found existing Store: {store.name}")

    # Create default segments
    segments_to_create = [
        {"name": "All Subscribers", "filter_config": {}},
        {"name": "VIP Customers", "filter_config": {"min_total_spent": 500}},
        {"name": "New This Month", "filter_config": {"last_purchase_days": 30}},
        {"name": "Abandoned Cart", "filter_config": {"tags": ["Abandoned Cart"]}},
        {"name": "Inactive 60+ Days", "filter_config": {"inactive_days": 60}}
    ]

    for seg in segments_to_create:
        segment, created_seg = EmailSegment.objects.get_or_create(
            store=store,
            name=seg["name"],
            defaults={"filter_config": seg["filter_config"], "is_default": True}
        )
        if created_seg:
            print(f"Created Segment: {segment.name}")

    # Import default contacts from CSV if they don't exist yet
    csv_path = "sample_contacts.csv"
    if os.path.exists(csv_path):
        with open(csv_path, "rb") as f:
            result = CsvContactImporter(store=store, default_accept_email_marketing=True).import_file(f)
            print(f"Imported CSV contacts: {result}")
    else:
        print("sample_contacts.csv not found, skipping contact import.")

    print("Seeding complete successfully!")

if __name__ == "__main__":
    seed()
