"""
Custom standardized pagination for DRF endpoints.
"""
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    """
    Standard pagination returning structured metadata.
    """
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        total_pages = self.page.paginator.num_pages if self.page else 1
        return Response({
            "success": True,
            "data": {
                "count": self.page.paginator.count,
                "total_pages": total_pages,
                "current_page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        })
