"""
Custom User Manager for Bus Ticket Management System.
Supports authentication with Phone or Email.
"""
from django.contrib.auth.base_user import BaseUserManager
from common.constants import UserRole


class UserManager(BaseUserManager):
    """
    Custom user model manager where phone number is the unique identifier
    for authentication instead of usernames, with optional email support.
    """

    def create_user(self, phone, email=None, password=None, role=UserRole.PASSENGER, **extra_fields):
        if not phone:
            raise ValueError("The Phone Number field is required.")

        phone = str(phone).strip().replace(" ", "").replace("-", "")

        if email:
            email = self.normalize_email(email).lower()

        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)

        user = self.model(
            phone=phone,
            email=email,
            role=role,
            **extra_fields
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, phone, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_verified', True)
        extra_fields.setdefault('role', UserRole.SUPER_ADMIN)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(phone, email=email, password=password, **extra_fields)
