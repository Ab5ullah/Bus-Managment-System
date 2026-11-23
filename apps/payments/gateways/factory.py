"""
Payment Gateway Factory for dynamic, pluggable provider resolution.
"""
from typing import Dict, Type
from .base import BasePaymentGateway
from .mock_gateway import MockPaymentGateway
from .stripe_gateway import StripePaymentGateway
from .cash_gateway import CashCounterGateway
from common.exceptions import BusinessLogicError


class PaymentGatewayFactory:
    """
    Factory resolving provider identifiers ('mock', 'stripe', 'cash', etc.)
    into concrete gateway instances.
    """
    _registry: Dict[str, Type[BasePaymentGateway]] = {
        'mock': MockPaymentGateway,
        'stripe': StripePaymentGateway,
        'cash': CashCounterGateway,
        'easypaisa': MockPaymentGateway,
        'jazzcash': MockPaymentGateway,
    }

    @classmethod
    def register(cls, name: str, gateway_cls: Type[BasePaymentGateway]) -> None:
        cls._registry[name.lower()] = gateway_cls

    @classmethod
    def get_gateway(cls, gateway_name: str) -> BasePaymentGateway:
        name = (gateway_name or 'mock').lower()
        gateway_cls = cls._registry.get(name)
        if not gateway_cls:
            raise BusinessLogicError(
                f"Unsupported payment gateway provider: '{gateway_name}'. "
                f"Available providers: {', '.join(cls._registry.keys())}"
            )
        return gateway_cls()

