"""
Verification script for Module 0: Project Setup & Core Foundation.
Can be executed via:
    python verify_module_0.py
"""
import os
import sys
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from rest_framework.test import APIClient
from django.urls import reverse


def run_verification():
    print("=" * 60)
    print("MODULE 0 VERIFICATION: PROJECT SETUP & CORE FOUNDATION")
    print("=" * 60)

    client = APIClient()

    # 1. Test Health Check Endpoint
    print("\n[1/3] Testing Health Check Endpoint (/api/health/)...")
    health_url = reverse('common:health-check')
    res = client.get(health_url)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    print("  Status code:", res.status_code)
    print("  Response JSON:\n", json.dumps(data, indent=2))
    assert data['success'] is True
    assert data['data']['status'] == 'healthy'
    assert data['data']['database'] == 'healthy'
    print("  --> PASS: Health Check is operational!")

    # 2. Test OpenAPI Documentation Endpoints
    print("\n[2/3] Testing OpenAPI / Swagger Endpoints...")
    schema_res = client.get(reverse('schema'))
    assert schema_res.status_code == 200
    print("  /api/schema/ -> HTTP 200 OK")

    docs_res = client.get(reverse('swagger-ui'))
    assert docs_res.status_code == 200
    print("  /api/docs/ (Swagger UI) -> HTTP 200 OK")

    redoc_res = client.get(reverse('redoc'))
    assert redoc_res.status_code == 200
    print("  /api/redoc/ (Redoc) -> HTTP 200 OK")
    print("  --> PASS: Documentation engines are live and rendering!")

    # 3. Test Custom Exception Formatting
    print("\n[3/3] Testing Custom Exception & Response Handler...")
    from common.exceptions import BusinessLogicError, custom_exception_handler
    from django.test import RequestFactory
    from rest_framework.views import APIView

    factory = RequestFactory()
    req = factory.get('/')
    exc = BusinessLogicError("Sample domain constraint test.")
    err_res = custom_exception_handler(exc, {"request": req, "view": APIView()})
    assert err_res.status_code == 400
    err_data = err_res.data
    print("  Standardized Error Output:\n", json.dumps(err_data, indent=2))
    assert err_data['success'] is False
    assert err_data['error']['code'] == 'BUSINESS_RULE_VIOLATION'
    print("  --> PASS: Standard error format strictly compliant!")

    print("\n" + "=" * 60)
    print("ALL MODULE 0 VERIFICATIONS PASSED SUCCESSFULLY (100%)")
    print("=" * 60)


if __name__ == '__main__':
    run_verification()
