import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'email_marketing_ms_django_backend.settings')
django.setup()

from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from Accounts.models import Client, ClientUser, UserRoleEnum, Store
from EmailMarketing.models import EmailSegment
from Accounts.BusinessLogic.CsvContactImporter import CsvContactImporter

User = get_user_model()

def seed():
    print("Seeding database...")

    # 1. Create Clients
    client_a, _ = Client.objects.get_or_create(name="Client A", defaults={"is_active": True})
    client_b, _ = Client.objects.get_or_create(name="Client B", defaults={"is_active": True})
    print("Created Clients: Client A, Client B")

    # 2. Create Staff User
    staff = User.objects.filter(email="staff@example.com").first()
    if not staff:
        staff = User.objects.create_superuser(
            email="staff@example.com",
            password="admin123",
            first_name="Staff",
            last_name="User"
        )
    else:
        staff.set_password("admin123")
        staff.save()
    Token.objects.get_or_create(user=staff)
    print("Created Staff User: staff@example.com / admin123")

    # 3. Create Admin User
    admin_user = User.objects.filter(email="admin@example.com").first()
    if not admin_user:
        admin_user = User.objects.create_user(
            email="admin@example.com",
            password="admin123",
            first_name="Admin",
            last_name="User",
            user_type="admin"
        )
    else:
        admin_user.user_type = "admin"
        admin_user.set_password("admin123")
        admin_user.save()
    Token.objects.get_or_create(user=admin_user)
    ClientUser.objects.get_or_create(user=admin_user, client=client_a, defaults={"role": UserRoleEnum.admin.value})
    print("Created Admin User: admin@example.com / admin123 (linked to Client A)")

    # 4. Create Operator User
    operator_user = User.objects.filter(email="operator@example.com").first()
    if not operator_user:
        operator_user = User.objects.create_user(
            email="operator@example.com",
            password="admin123",
            first_name="Operator",
            last_name="User",
            user_type="operator"
        )
    else:
        operator_user.user_type = "operator"
        operator_user.set_password("admin123")
        operator_user.save()
    Token.objects.get_or_create(user=operator_user)
    ClientUser.objects.get_or_create(user=operator_user, client=client_a, defaults={"role": UserRoleEnum.member.value})
    print("Created Operator User: operator@example.com / admin123 (linked to Client A)")

    # 5. Create Stores/Shops
    store_alpha, _ = Store.objects.get_or_create(
        client=client_a,
        shop_url="store-alpha.myshopify.com",
        defaults={
            "name": "Store Alpha",
            "is_active": True,
            "email_provider": "smtp",
            "default_from_email": "alpha@example.com",
            "default_from_name": "Alpha Brand"
        }
    )
    store_beta, _ = Store.objects.get_or_create(
        client=client_a,
        shop_url="store-beta.myshopify.com",
        defaults={
            "name": "Store Beta",
            "is_active": True,
            "email_provider": "smtp",
            "default_from_email": "beta@example.com",
            "default_from_name": "Beta Brand"
        }
    )
    print("Created Stores: Store Alpha, Store Beta under Client A")

    # 6. Assign Store Alpha to Operator
    operator_user.assigned_stores.set([store_alpha])
    print("Assigned Store Alpha to Operator User")

    # 7. Create Default Segments & Import Contacts for both stores
    segments_to_create = [
        {"name": "All Subscribers", "filter_config": {}},
        {"name": "VIP Customers", "filter_config": {"min_total_spent": 500}},
        {"name": "New This Month", "filter_config": {"last_purchase_days": 30}},
        {"name": "Abandoned Cart", "filter_config": {"tags": ["Abandoned Cart"]}},
        {"name": "Inactive 60+ Days", "filter_config": {"inactive_days": 60}}
    ]

    for store in [store_alpha, store_beta]:
        for seg in segments_to_create:
            EmailSegment.objects.get_or_create(
                store=store,
                name=seg["name"],
                defaults={"filter_config": seg["filter_config"], "is_default": True}
            )

        csv_path = "sample_contacts.csv"
        if os.path.exists(csv_path):
            with open(csv_path, "rb") as f:
                CsvContactImporter(store=store, default_accept_email_marketing=True).import_file(f)
        print(f"Seeded segments & sample contacts for {store.name}")

    print("Seeding complete successfully!")

if __name__ == "__main__":
    seed()
