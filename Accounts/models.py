import secrets
from enum import Enum

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from rest_framework.authtoken.models import Token


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, blank=True, null=True)

    class Meta:
        abstract = True


class UserRoleEnum(Enum):
    admin = "admin"
    member = "member"


class Client(TimeStampedModel):
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Store(TimeStampedModel):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="stores")
    name = models.CharField(max_length=255)
    shop_url = models.CharField(max_length=500)
    api_key = models.CharField(max_length=64, unique=True, editable=False, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    email_provider = models.CharField(max_length=50, default="sendgrid")
    sendgrid_api_key = models.CharField(max_length=500, blank=True, null=True)
    smtp_host = models.CharField(max_length=255, blank=True, null=True)
    smtp_port = models.IntegerField(default=587)
    smtp_username = models.CharField(max_length=255, blank=True, null=True)
    smtp_password = models.CharField(max_length=255, blank=True, null=True)
    smtp_use_tls = models.BooleanField(default=True)
    default_from_email = models.EmailField(blank=True, null=True)
    default_from_name = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        unique_together = ("client", "shop_url")
        indexes = [
            models.Index(fields=["client", "is_active"]),
            models.Index(fields=["api_key"]),
        ]

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_hex(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        extra_fields.setdefault("username", email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser, TimeStampedModel):
    email = models.EmailField(unique=True)
    user_type = models.CharField(
        max_length=20,
        choices=[('admin', 'Admin'), ('operator', 'Operator')],
        default='admin'
    )
    assigned_stores = models.ManyToManyField(Store, blank=True, related_name="assigned_users")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def save(self, *args, **kwargs):
        if not self.username:
            self.username = self.email
        super().save(*args, **kwargs)
        if self.pk:
            Token.objects.get_or_create(user=self)

    def __str__(self):
        return self.email


class ClientUser(TimeStampedModel):
    """Links a user to a client with a per-client role."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="client_memberships")
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="client_users")
    role = models.CharField(max_length=20, default=UserRoleEnum.member.value)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("user", "client")
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["client", "is_active"]),
        ]

    @property
    def is_admin(self):
        return self.role == UserRoleEnum.admin.value

    def __str__(self):
        return f"{f'{self.user.email} @ {self.client.name}'} ({self.role})"


class Contact(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="contacts")
    external_id = models.CharField(max_length=200, blank=True, null=True)
    email = models.EmailField()
    first_name = models.CharField(max_length=200, blank=True, null=True)
    last_name = models.CharField(max_length=200, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    city = models.CharField(max_length=200, blank=True, null=True)
    country = models.CharField(max_length=200, blank=True, null=True)
    tags = models.JSONField(default=list, blank=True)
    accept_email_marketing = models.BooleanField(default=False)
    accept_email_marketing_at = models.DateTimeField(blank=True, null=True)
    total_orders = models.IntegerField(default=0)
    total_spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    last_order_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ("store", "email")
        indexes = [
            models.Index(fields=["store", "accept_email_marketing"]),
            models.Index(fields=["store", "email"]),
            models.Index(fields=["store", "last_order_at"]),
        ]

    def __str__(self):
        return self.email
