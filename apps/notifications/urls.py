"""
URL routing for Notifications & Housekeeping admin endpoints.
"""
from django.urls import path
from .views import (
    NotificationLogListView,
    TriggerCleanupSeatLocksView,
    TriggerDepartureRemindersView,
)

urlpatterns = [
    path('logs/', NotificationLogListView.as_view(), name='notification-log-list'),
    path('cleanup-locks/', TriggerCleanupSeatLocksView.as_view(), name='notification-cleanup-locks'),
    path('send-reminders/', TriggerDepartureRemindersView.as_view(), name='notification-send-reminders'),
]
