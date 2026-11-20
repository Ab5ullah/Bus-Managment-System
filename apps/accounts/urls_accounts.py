from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProfileView, SavedPassengerViewSet

app_name = 'accounts'

router = DefaultRouter()
router.register('saved-passengers', SavedPassengerViewSet, basename='saved-passengers')

urlpatterns = [
    path('profile/', ProfileView.as_view(), name='profile'),
    path('', include(router.urls)),
]
