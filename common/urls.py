from django.urls import path
from .views import HealthCheckView, AppConfigView

app_name = 'common'

urlpatterns = [
    path('health/', HealthCheckView.as_view(), name='health-check'),
    path('app-config/', AppConfigView.as_view(), name='app-config'),
]
