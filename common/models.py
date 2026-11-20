"""
Base and reusable models for the Bus Ticket Management System.
"""
import uuid
from django.db import models


class TimeStampedModel(models.Model):
    """
    Abstract base model that provides self-updating
    'created_at' and 'updated_at' fields.
    """
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']


class UUIDModel(models.Model):
    """
    Abstract base model that uses a cryptographically secure UUIDv4 as primary key.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    class Meta:
        abstract = True


class IsActiveModel(models.Model):
    """
    Abstract base model providing an 'is_active' soft status flag.
    """
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel, IsActiveModel):
    """
    Standard base model combining UUID primary key, timestamps, and active status flag.
    """
    class Meta:
        abstract = True
        ordering = ['-created_at']
