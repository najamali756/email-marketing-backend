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
        return f"{self.user.email} @ {self.client.name} ({self.role})"
