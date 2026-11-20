from django.urls import path
from .views import (
    RegisterView,
    VerifyOTPView,
    LoginView,
    CustomTokenRefreshView,
    LogoutView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
)

app_name = 'auth'

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('login/', LoginView.as_view(), name='login'),
    path('refresh/', CustomTokenRefreshView.as_view(), name='refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('password-reset-request/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
]
