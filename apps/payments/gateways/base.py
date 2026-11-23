"""
Abstract base class for all pluggable payment gateway providers.
"""
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, Any, Optional


class BasePaymentGateway(ABC):
    """
    Contract defining all required payment gateway capabilities:
    - Initiation of payment intents / checkout sessions
    - Cryptographic webhook validation and payload decoding
    - Refund processing
    """
    gateway_name: str = 'base'

    @abstractmethod
    def initiate_payment(
        self,
        payment,
        return_url: str = '',
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Creates a checkout session or payment intent with the external gateway.
        Returns a dict containing:
        - gateway_reference: str
        - checkout_url: str
        - raw_data: dict
        """
        pass

    @abstractmethod
    def verify_webhook(
        self,
        payload: Dict[str, Any],
        signature: str = '',
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Verifies external webhook payload and authenticates the signature.
        Returns a dict containing:
        - is_valid: bool
        - event_type: str ('SUCCESS', 'FAILED', 'REFUND')
        - idempotency_key: str
        - gateway_reference: str
        - amount: Optional[Decimal]
        - failure_reason: str
        - raw_data: dict
        """
        pass

    @abstractmethod
    def process_refund(
        self,
        payment,
        amount: Decimal,
        reason: str = ''
    ) -> Dict[str, Any]:
        """
        Processes a full or partial refund for a captured payment.
        Returns a dict containing:
        - success: bool
        - refund_reference: str
        - refunded_amount: Decimal
        - raw_data: dict
        """
        pass
