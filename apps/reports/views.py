"""
API Views for Module 11: Reports & Analytics Engine.
Strictly protected by IsOperator RBAC permission.
"""
from decimal import Decimal, InvalidOperation
from rest_framework.views import APIView
from rest_framework import status
from drf_spectacular.utils import extend_schema, OpenApiParameter

from accounts.permissions import IsOperator
from common.exceptions import BusinessLogicError
from common.responses import api_success, api_error
from .services import AnalyticsService
from .exporters import ReportExporter


class RevenueReportView(APIView):
    """
    GET /api/admin/reports/revenue/?start_date=&end_date=&group_by=day
    """
    permission_classes = [IsOperator]

    @extend_schema(
        summary="Revenue & Financial Analytics",
        parameters=[
            OpenApiParameter(name='start_date', type=str, required=False),
            OpenApiParameter(name='end_date', type=str, required=False),
            OpenApiParameter(name='group_by', type=str, required=False, description="day, week, or month"),
        ],
        tags=["Reports & Analytics"]
    )
    def get(self, request):
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        group_by = request.query_params.get('group_by', 'day')

        try:
            report = AnalyticsService.get_revenue_report(
                start_date=start_date,
                end_date=end_date,
                group_by=group_by
            )
            return api_success(data=report, message="Revenue analytics generated successfully.")
        except BusinessLogicError as exc:
            return api_error(message=str(exc), code="BUSINESS_RULE_VIOLATION", status_code=status.HTTP_400_BAD_REQUEST)


class OccupancyReportView(APIView):
    """
    GET /api/admin/reports/occupancy/?trip_id=&route_id=
    """
    permission_classes = [IsOperator]

    @extend_schema(
        summary="Load Factor & Seat Occupancy Analytics",
        parameters=[
            OpenApiParameter(name='trip_id', type=str, required=False),
            OpenApiParameter(name='route_id', type=str, required=False),
            OpenApiParameter(name='start_date', type=str, required=False),
            OpenApiParameter(name='end_date', type=str, required=False),
        ],
        tags=["Reports & Analytics"]
    )
    def get(self, request):
        try:
            report = AnalyticsService.get_occupancy_report(
                trip_id=request.query_params.get('trip_id'),
                route_id=request.query_params.get('route_id'),
                start_date=request.query_params.get('start_date'),
                end_date=request.query_params.get('end_date'),
            )
            return api_success(data=report, message="Occupancy load factor analytics generated successfully.")
        except BusinessLogicError as exc:
            return api_error(message=str(exc), code="BUSINESS_RULE_VIOLATION", status_code=status.HTTP_400_BAD_REQUEST)


class RoutePerformanceReportView(APIView):
    """
    GET /api/admin/reports/route-performance/
    """
    permission_classes = [IsOperator]

    @extend_schema(
        summary="Top Routes & Peak Travel Hours",
        tags=["Reports & Analytics"]
    )
    def get(self, request):
        try:
            report = AnalyticsService.get_route_performance_report(
                start_date=request.query_params.get('start_date'),
                end_date=request.query_params.get('end_date'),
            )
            return api_success(data=report, message="Route performance and peak hours generated successfully.")
        except BusinessLogicError as exc:
            return api_error(message=str(exc), code="BUSINESS_RULE_VIOLATION", status_code=status.HTTP_400_BAD_REQUEST)


class AgentSalesReportView(APIView):
    """
    GET /api/admin/reports/agent-sales/
    """
    permission_classes = [IsOperator]

    @extend_schema(
        summary="Counter Agent Sales & Commission Reconciliation",
        parameters=[
            OpenApiParameter(name='start_date', type=str, required=False),
            OpenApiParameter(name='end_date', type=str, required=False),
            OpenApiParameter(name='commission_rate', type=str, required=False),
        ],
        tags=["Reports & Analytics"]
    )
    def get(self, request):
        rate_str = request.query_params.get('commission_rate', '5.00')
        try:
            commission_rate = Decimal(str(rate_str))
        except InvalidOperation:
            return api_error(message="Invalid commission_rate decimal value.", status_code=status.HTTP_400_BAD_REQUEST)

        try:
            report = AnalyticsService.get_agent_sales_report(
                start_date=request.query_params.get('start_date'),
                end_date=request.query_params.get('end_date'),
                commission_rate=commission_rate,
            )
            return api_success(data=report, message="Counter agent sales and commissions reconciled successfully.")
        except BusinessLogicError as exc:
            return api_error(message=str(exc), code="BUSINESS_RULE_VIOLATION", status_code=status.HTTP_400_BAD_REQUEST)


class ReportExportView(APIView):
    """
    GET /api/admin/reports/export/?type=revenue&format=csv
    Streams downloadable CSV or Excel (.xlsx) file for BI reports.
    """
    permission_classes = [IsOperator]

    def perform_content_negotiation(self, request, force=False):
        # Bypass DRF URL_FORMAT_OVERRIDE ('?format=csv|xlsx') lookup so ?format= works directly
        renderer = self.get_renderers()[0]
        return (renderer, renderer.media_type)

    @extend_schema(
        summary="Export Business Report (CSV / Excel)",
        parameters=[
            OpenApiParameter(name='type', type=str, required=False, description="revenue, occupancy, agent-sales, or route-performance"),
            OpenApiParameter(name='format', type=str, required=False, description="csv or xlsx"),
            OpenApiParameter(name='start_date', type=str, required=False),
            OpenApiParameter(name='end_date', type=str, required=False),
            OpenApiParameter(name='group_by', type=str, required=False),
        ],
        tags=["Reports & Analytics"]
    )
    def get(self, request):
        report_type = (request.query_params.get('type') or 'revenue').lower()
        export_format = (request.query_params.get('format') or 'csv').lower()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        if report_type not in ('revenue', 'occupancy', 'agent-sales', 'route-performance'):
            return api_error(
                message="Unsupported report type. Choose: revenue, occupancy, agent-sales, route-performance.",
                status_code=status.HTTP_400_BAD_REQUEST
            )
        if export_format not in ('csv', 'xlsx', 'excel'):
            return api_error(
                message="Unsupported export format. Choose: csv or xlsx.",
                status_code=status.HTTP_400_BAD_REQUEST
            )

        try:
            if report_type == 'revenue':
                data = AnalyticsService.get_revenue_report(
                    start_date=start_date,
                    end_date=end_date,
                    group_by=request.query_params.get('group_by', 'day')
                )
            elif report_type == 'occupancy':
                data = AnalyticsService.get_occupancy_report(
                    trip_id=request.query_params.get('trip_id'),
                    route_id=request.query_params.get('route_id'),
                    start_date=start_date,
                    end_date=end_date
                )
            elif report_type == 'agent-sales':
                data = AnalyticsService.get_agent_sales_report(
                    start_date=start_date,
                    end_date=end_date
                )
            else:
                data = AnalyticsService.get_route_performance_report(
                    start_date=start_date,
                    end_date=end_date
                )
        except BusinessLogicError as exc:
            return api_error(message=str(exc), code="BUSINESS_RULE_VIOLATION", status_code=status.HTTP_400_BAD_REQUEST)

        title, headers, rows = ReportExporter.extract_tabular(report_type, data)
        filename = f"busms_{report_type}_report"

        if export_format in ('xlsx', 'excel'):
            return ReportExporter.to_excel_response(filename, title, headers, rows)
        return ReportExporter.to_csv_response(filename, headers, rows)
