"""
API Views — Secure Transaction Voucher Engine
================================================
Thin DRF views. ZERO business logic. All mutations delegated to ``services.py``.

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .serializers import (
    CreateVoucherSerializer,
    DeactivateVoucherSerializer,
    RedeemVoucherSerializer,
    RedemptionRecordSerializer,
    VoucherSerializer,
)


def _get_client_ip(request) -> str | None:
    """Extract client IP from request, respecting reverse proxies."""
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class VoucherCreateView(APIView):
    """
    POST /api/vouchers/create/

    Mint a new voucher. Requires authentication.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = CreateVoucherSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        voucher = services.create_voucher(
            value=serializer.validated_data["value"],
            issued_to_id=serializer.validated_data.get("issued_to"),
            created_by_id=request.user.pk,
            expires_at=serializer.validated_data.get("expires_at"),
            metadata=serializer.validated_data.get("metadata", {}),
        )

        return Response(
            VoucherSerializer(voucher).data,
            status=status.HTTP_201_CREATED,
        )


class VoucherDetailView(APIView):
    """
    GET /api/vouchers/<code>/

    Retrieve a voucher by its code. Read-only, no lock acquired.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, code: str):
        voucher = services.get_voucher_by_code(code)
        return Response(VoucherSerializer(voucher).data)


class VoucherRedeemView(APIView):
    """
    POST /api/vouchers/redeem/

    Atomically redeem value from a voucher. This is the critical path.
    All concurrency protections are enforced by the service layer.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = RedeemVoucherSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        record = services.redeem_voucher(
            code=serializer.validated_data["code"],
            amount=serializer.validated_data["amount"],
            redeemed_by_id=request.user.pk,
            ip_address=_get_client_ip(request),
        )

        return Response(
            RedemptionRecordSerializer(record).data,
            status=status.HTTP_200_OK,
        )


class VoucherDeactivateView(APIView):
    """
    POST /api/vouchers/deactivate/

    Permanently deactivate a voucher. Irreversible.
    Requires admin-level permissions.
    """

    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        serializer = DeactivateVoucherSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        voucher = services.deactivate_voucher(
            code=serializer.validated_data["code"],
        )

        return Response(
            VoucherSerializer(voucher).data,
            status=status.HTTP_200_OK,
        )


class VoucherRedemptionHistoryView(APIView):
    """
    GET /api/vouchers/<code>/history/

    Return the full audit trail for a voucher.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, code: str):
        voucher = services.get_voucher_by_code(code)
        records = voucher.redemptions.all()
        return Response(RedemptionRecordSerializer(records, many=True).data)
