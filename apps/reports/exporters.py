"""
CSV and Excel (.xlsx) export utilities for Module 11: Reports & Analytics Engine.
"""
import csv
import io
from typing import List, Tuple, Any
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Font


class ReportExporter:
    """
    Streams tabular report datasets as RFC-4180 CSV or styled Excel (.xlsx) files.
    """

    @staticmethod
    def to_csv_response(
        filename: str,
        headers: List[str],
        rows: List[List[Any]]
    ) -> HttpResponse:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'

        writer = csv.writer(response)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([str(v) if v is not None else '' for v in row])

        return response

    @staticmethod
    def to_excel_response(
        filename: str,
        sheet_title: str,
        headers: List[str],
        rows: List[List[Any]]
    ) -> HttpResponse:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_title[:31]

        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)

        for row in rows:
            cleaned = [float(v) if hasattr(v, 'quantize') else v for v in row]
            ws.append(cleaned)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        response = HttpResponse(
            buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}.xlsx"'
        return response

    @classmethod
    def extract_tabular(cls, report_type: str, data: dict) -> Tuple[str, List[str], List[List[Any]]]:
        """Normalizes report dict output into (title, headers, rows) for export."""
        if report_type == 'revenue':
            headers = [
                'Period', 'Bookings', 'Gross Fare (PKR)', 'Discounts (PKR)',
                'Tax (PKR)', 'Service Fees (PKR)', 'Total Collected (PKR)',
                'Refunds Issued (PKR)', 'Net Revenue (PKR)'
            ]
            rows = [
                [
                    r['period'], r['bookings_count'], r['gross_fare'], r['discounts'],
                    r['tax_collected'], r['service_fees'], r['total_collected'],
                    r['refunds_issued'], r['net_revenue']
                ]
                for r in data.get('series', [])
            ]
            return 'Revenue Report', headers, rows

        if report_type == 'occupancy':
            headers = [
                'Trip Number', 'Route', 'Bus', 'Departure', 'Status',
                'Capacity', 'Booked Segments', 'Distance (km)',
                'Passenger-km', 'Seat-km', 'Load Factor (%)'
            ]
            rows = [
                [
                    t['trip_number'], t['route_code'], t['bus_registration'],
                    t['departure_datetime'], t['status'], t['bus_capacity'],
                    t['booked_segments_count'], t['route_distance_km'],
                    t['passenger_km'], t['seat_km'], t['load_factor_percentage']
                ]
                for t in data.get('trips', [])
            ]
            return 'Occupancy Report', headers, rows

        if report_type == 'agent-sales':
            headers = [
                'Agent Name', 'Phone', 'Role', 'Bookings', 'Seats Sold',
                'Gross Sales (PKR)', 'Net Collected (PKR)',
                'Commission Rate (%)', 'Commission Earned (PKR)'
            ]
            rows = [
                [
                    a['agent_name'], a['agent_phone'], a['role'], a['bookings_count'],
                    a['seats_sold'], a['gross_sales'], a['net_collected'],
                    a['commission_rate_percentage'], a['commission_earned']
                ]
                for a in data.get('agents', [])
            ]
            return 'Agent Sales Report', headers, rows

        if report_type == 'route-performance':
            headers = ['Route Code', 'Route Name', 'Seats Sold', 'Fare Revenue (PKR)']
            rows = [
                [r['route_code'], r['route_name'], r['seats_sold'], r['fare_revenue']]
                for r in data.get('top_routes', [])
            ]
            return 'Route Performance', headers, rows

        return 'Report', [], []
