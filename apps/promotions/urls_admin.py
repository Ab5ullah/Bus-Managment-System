"""
Admin URL configuration for Module 10: Promotions & Dynamic Pricing CRUD.
Mounted at /api/admin/promotions/
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CouponAdminViewSet, DynamicPricingRuleViewSet

router = DefaultRouter()
router.register(r'coupons', CouponAdminViewSet, basename='admin-promotions-coupon')
router.register(r'dynamic-rules', DynamicPricingRuleViewSet, basename='admin-promotions-dynamic-rule')

urlpatterns = [
    path('', include(router.urls)),
]
