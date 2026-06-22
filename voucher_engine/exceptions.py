"""
Custom Security Exceptions
============================
Typed exception hierarchy for deterministic error handling.
All exceptions carry HTTP-safe messages — no internal state leakage.

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from rest_framework import status
from rest_framework.exceptions import APIException


class VoucherEngineException(APIException):
    """Base exception for all voucher engine errors."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "A voucher engine error occurred."
    default_code = "voucher_engine_error"


class VoucherNotFoundException(VoucherEngineException):
    """Raised when a voucher code resolves to no record."""

    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "The requested voucher does not exist."
    default_code = "voucher_not_found"


class VoucherInactiveException(VoucherEngineException):
    """Raised when an operation targets a deactivated voucher."""

    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This voucher has been deactivated."
    default_code = "voucher_inactive"


class VoucherAlreadyRedeemedException(VoucherEngineException):
    """Raised on attempted double-spend."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This voucher has already been redeemed."
    default_code = "voucher_already_redeemed"


class VoucherExpiredException(VoucherEngineException):
    """Raised when a voucher is past its expiration window."""

    status_code = status.HTTP_410_GONE
    default_detail = "This voucher has expired."
    default_code = "voucher_expired"


class VoucherCreationException(VoucherEngineException):
    """Raised when voucher generation fails integrity checks."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "Failed to generate a valid voucher."
    default_code = "voucher_creation_failed"


class InsufficientVoucherValueException(VoucherEngineException):
    """Raised when redemption amount exceeds remaining voucher value."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Redemption amount exceeds the voucher balance."
    default_code = "insufficient_voucher_value"


class ConcurrencyViolationException(VoucherEngineException):
    """Raised when a row-lock acquisition fails under contention."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Concurrent operation detected. Retry the request."
    default_code = "concurrency_violation"
