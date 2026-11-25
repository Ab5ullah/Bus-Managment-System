"""
Django Admin registration for NotificationLog.
"""
from django.contrib import admin
from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('channel', 'recipient', 'template_name', 'status', 'sent_at', 'created_at')
    list_filter = ('channel', 'status', 'template_name')
    search_fields = ('recipient', 'subject', 'body')
    readonly_fields = (
        'id', 'user', 'recipient', 'channel', 'subject', 'body',
        'template_name', 'status', 'error_message', 'metadata', 'sent_at', 'created_at', 'updated_at'
    )
    ordering = ('-created_at',)
