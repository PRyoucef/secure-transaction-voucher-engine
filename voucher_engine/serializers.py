"""
DRF Serializers — Secure Transaction Voucher Engine
=====================================================
Input validation and output shaping. No business logic here.

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from decimal import Decimal

from rest_framework import serializers

from .models import RedemptionRecord, Voucher


# ═══════════════════════════════════════════════════════════════════════════
#  OUTPUT SERIALIZERS (Read-Only)
# ═══════════════════════════════════════════════════════════════════════════


class VoucherSerializer(serializers.ModelSerializer):
    """Public representation of a voucher. Never exposes internal IDs."""

    is_expired = serializers.BooleanField(read_only=True)
    is_usable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Voucher
        fields = [
            "id",
            "code",
            "value",
            "remaining_value",
            "is_active",
            "is_redeemed",
            "is_expired",
            "is_usable",
            "created_at",
            "updated_at",
            "expires_at",
            "redeemed_at",
            "deactivated_at",
            "metadata",
        ]
        read_only_fields = fields


class RedemptionRecordSerializer(serializers.ModelSerializer):
    """Read-only audit record."""

    voucher_code = serializers.CharField(source="voucher.code", read_only=True)

    class Meta:
        model = RedemptionRecord
        fields = [
            "id",
            "voucher_code",
            "amount",
            "status",
            "redeemed_by",
            "redeemed_at",
            "ip_address",
            "notes",
        ]
        read_only_fields = fields


# ═══════════════════════════════════════════════════════════════════════════
#  INPUT SERIALIZERS (Write)
# ═══════════════════════════════════════════════════════════════════════════


class CreateVoucherSerializer(serializers.Serializer):
    """Validates input for voucher creation."""

    value = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Face value of the voucher (must be > 0).",
    )
    issued_to = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="User PK to issue the voucher to.",
    )
    expires_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Optional ISO-8601 expiration timestamp.",
    )
    metadata = serializers.JSONField(
        required=False,
        default=dict,
        help_text="Arbitrary metadata dict.",
    )


class RedeemVoucherSerializer(serializers.Serializer):
    """Validates input for voucher redemption."""

    code = serializers.CharField(
        max_length=64,
        help_text="The voucher code to redeem.",
    )
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="The amount to redeem from the voucher.",
    )


class DeactivateVoucherSerializer(serializers.Serializer):
    """Validates input for voucher deactivation."""

    code = serializers.CharField(
        max_length=64,
        help_text="The voucher code to deactivate.",
    )
