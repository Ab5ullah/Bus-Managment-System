"""
URL configuration for Module 10: Promotions, Coupons & Dynamic Pricing.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ValidateCouponView,
    ApplyCouponView,
    RemoveCouponView,
    DynamicPriceCheckView,
    CouponAdminViewSet,
    DynamicPricingRuleViewSet,
)

admin_router = DefaultRouter()
admin_router.register(r'coupons', CouponAdminViewSet, basename='admin-coupon')
admin_router.register(r'dynamic-rules', DynamicPricingRuleViewSet, basename='admin-dynamic-rule')

urlpatterns = [
    path('validate-coupon/', ValidateCouponView.as_view(), name='promotion-validate-coupon'),
    path('apply-coupon/', ApplyCouponView.as_view(), name='promotion-apply-coupon'),
    path('remove-coupon/', RemoveCouponView.as_view(), name='promotion-remove-coupon'),
    path('dynamic-price/', DynamicPriceCheckView.as_view(), name='promotion-dynamic-price'),

    # Admin CRUD under /api/promotions/admin/...
    path('admin/', include(admin_router.urls)),
]
