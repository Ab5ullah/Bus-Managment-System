"""
Celery application configuration for Bus Ticket Management System (busms).
"""
import os
import sys
from pathlib import Path
from celery import Celery

# Ensure apps/ is on sys.path for Celery worker processes
BASE_DIR = Path(__file__).resolve().parent.parent
APPS_DIR = str(BASE_DIR / 'apps')
if APPS_DIR not in sys.path:
    sys.path.insert(0, APPS_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('busms')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
