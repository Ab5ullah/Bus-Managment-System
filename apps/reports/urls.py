"""
URL configuration for Module 11: Reports & Analytics Engine.
Mounted at /api/admin/reports/
"""
from django.urls import path
from .views import (
    RevenueReportView,
    OccupancyReportView,
    RoutePerformanceReportView,
    AgentSalesReportView,
    ReportExportView,
)

urlpatterns = [
    path('revenue/', RevenueReportView.as_view(), name='reports-revenue'),
    path('occupancy/', OccupancyReportView.as_view(), name='reports-occupancy'),
    path('route-performance/', RoutePerformanceReportView.as_view(), name='reports-route-performance'),
    path('agent-sales/', AgentSalesReportView.as_view(), name='reports-agent-sales'),
    path('export/', ReportExportView.as_view(), name='reports-export'),
]
