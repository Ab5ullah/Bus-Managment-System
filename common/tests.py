from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from common.exceptions import BusinessLogicError, ResourceConflictError, custom_exception_handler
from rest_framework.exceptions import ValidationError
from django.test import RequestFactory
from rest_framework.views import APIView


class HealthCheckAPITestCase(APITestCase):
    def test_health_check_endpoint(self):
        url = reverse('common:health-check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['data']['status'], 'healthy')
        self.assertEqual(data['data']['database'], 'healthy')


class DocumentationAPITestCase(APITestCase):
    def test_openapi_schema(self):
        url = reverse('schema')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_swagger_ui(self):
        url = reverse('swagger-ui')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_redoc_ui(self):
        url = reverse('redoc')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ExceptionHandlerTestCase(APITestCase):
    def test_validation_error_structure(self):
        factory = RequestFactory()
        request = factory.get('/')
        exc = ValidationError({"field": ["This field is required."]})
        context = {"request": request, "view": APIView()}
        response = custom_exception_handler(exc, context)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['error']['code'], 'VALIDATION_ERROR')

    def test_business_logic_error_structure(self):
        factory = RequestFactory()
        request = factory.get('/')
        exc = BusinessLogicError("Rule violated")
        context = {"request": request, "view": APIView()}
        response = custom_exception_handler(exc, context)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'BUSINESS_RULE_VIOLATION')
