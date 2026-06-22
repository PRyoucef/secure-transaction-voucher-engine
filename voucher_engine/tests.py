"""
Test Suite — Secure Transaction Voucher Engine
================================================
Covers the critical path: creation, redemption, double-spend prevention,
deactivation, expiry, and insufficient funds.

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from voucher_engine import services
from voucher_engine.exceptions import (
    InsufficientVoucherValueException,
    VoucherAlreadyRedeemedException,
    VoucherCreationException,
    VoucherExpiredException,
    VoucherInactiveException,
    VoucherNotFoundException,
)
from voucher_engine.models import RedemptionRecord, Voucher

User = get_user_model()


class VoucherCreationTests(TestCase):
    """Test the voucher minting pipeline."""

    def test_create_voucher_success(self):
        voucher = services.create_voucher(value=Decimal("100.00"))
        self.assertIsNotNone(voucher.id)
        self.assertIsNotNone(voucher.code)
        self.assertEqual(len(voucher.code), 32)
        self.assertEqual(voucher.value, Decimal("100.00"))
        self.assertEqual(voucher.remaining_value, Decimal("100.00"))
        self.assertTrue(voucher.is_active)
        self.assertFalse(voucher.is_redeemed)

    def test_create_voucher_with_expiry(self):
        future = timezone.now() + timezone.timedelta(days=30)
        voucher = services.create_voucher(
            value=Decimal("50.00"),
            expires_at=future,
        )
        self.assertEqual(voucher.expires_at, future)
        self.assertFalse(voucher.is_expired)

    def test_create_voucher_zero_value_raises(self):
        with self.assertRaises(VoucherCreationException):
            services.create_voucher(value=Decimal("0.00"))

    def test_create_voucher_negative_value_raises(self):
        with self.assertRaises(VoucherCreationException):
            services.create_voucher(value=Decimal("-10.00"))

    def test_voucher_codes_are_unique(self):
        v1 = services.create_voucher(value=Decimal("10.00"))
        v2 = services.create_voucher(value=Decimal("10.00"))
        self.assertNotEqual(v1.code, v2.code)


class VoucherRetrievalTests(TestCase):
    """Test read-only retrieval paths."""

    def setUp(self):
        self.voucher = services.create_voucher(value=Decimal("100.00"))

    def test_get_by_code_success(self):
        found = services.get_voucher_by_code(self.voucher.code)
        self.assertEqual(found.id, self.voucher.id)

    def test_get_by_code_not_found(self):
        with self.assertRaises(VoucherNotFoundException):
            services.get_voucher_by_code("nonexistent-code")

    def test_get_by_id_success(self):
        found = services.get_voucher_by_id(self.voucher.id)
        self.assertEqual(found.code, self.voucher.code)

    def test_get_by_id_not_found(self):
        import uuid

        with self.assertRaises(VoucherNotFoundException):
            services.get_voucher_by_id(uuid.uuid4())


class VoucherRedemptionTests(TransactionTestCase):
    """Test the atomic redemption pipeline."""

    def setUp(self):
        self.voucher = services.create_voucher(value=Decimal("100.00"))

    def test_partial_redemption(self):
        record = services.redeem_voucher(
            code=self.voucher.code,
            amount=Decimal("30.00"),
        )
        self.assertEqual(record.status, RedemptionRecord.Status.SUCCESS)
        self.assertEqual(record.amount, Decimal("30.00"))

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.remaining_value, Decimal("70.00"))
        self.assertFalse(self.voucher.is_redeemed)
        self.assertIsNone(self.voucher.redeemed_at)

    def test_full_redemption(self):
        record = services.redeem_voucher(
            code=self.voucher.code,
            amount=Decimal("100.00"),
        )
        self.assertEqual(record.status, RedemptionRecord.Status.SUCCESS)

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.remaining_value, Decimal("0.00"))
        self.assertTrue(self.voucher.is_redeemed)
        self.assertIsNotNone(self.voucher.redeemed_at)

    def test_multi_step_redemption(self):
        services.redeem_voucher(code=self.voucher.code, amount=Decimal("40.00"))
        services.redeem_voucher(code=self.voucher.code, amount=Decimal("40.00"))
        record = services.redeem_voucher(
            code=self.voucher.code, amount=Decimal("20.00")
        )

        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.remaining_value, Decimal("0.00"))
        self.assertTrue(self.voucher.is_redeemed)
        self.assertEqual(self.voucher.redemptions.count(), 3)

    def test_double_spend_prevention(self):
        """Once fully redeemed, subsequent attempts must fail."""
        services.redeem_voucher(
            code=self.voucher.code, amount=Decimal("100.00")
        )
        with self.assertRaises(VoucherAlreadyRedeemedException):
            services.redeem_voucher(
                code=self.voucher.code, amount=Decimal("1.00")
            )
        # Verify the failed attempt was logged
        self.assertEqual(self.voucher.redemptions.count(), 2)
        failed = self.voucher.redemptions.filter(
            status=RedemptionRecord.Status.FAILED_REDEEMED
        )
        self.assertEqual(failed.count(), 1)

    def test_insufficient_funds(self):
        with self.assertRaises(InsufficientVoucherValueException):
            services.redeem_voucher(
                code=self.voucher.code, amount=Decimal("150.00")
            )

    def test_redeem_nonexistent_code(self):
        with self.assertRaises(VoucherNotFoundException):
            services.redeem_voucher(
                code="fake-code", amount=Decimal("10.00")
            )

    def test_redeem_expired_voucher(self):
        past = timezone.now() - timezone.timedelta(days=1)
        expired = services.create_voucher(
            value=Decimal("50.00"), expires_at=past
        )
        with self.assertRaises(VoucherExpiredException):
            services.redeem_voucher(
                code=expired.code, amount=Decimal("10.00")
            )

    def test_redeem_inactive_voucher(self):
        services.deactivate_voucher(code=self.voucher.code)
        with self.assertRaises(VoucherInactiveException):
            services.redeem_voucher(
                code=self.voucher.code, amount=Decimal("10.00")
            )

    def test_redemption_records_ip_address(self):
        record = services.redeem_voucher(
            code=self.voucher.code,
            amount=Decimal("10.00"),
            ip_address="192.168.1.42",
        )
        self.assertEqual(record.ip_address, "192.168.1.42")


class VoucherDeactivationTests(TransactionTestCase):
    """Test administrative deactivation."""

    def setUp(self):
        self.voucher = services.create_voucher(value=Decimal("100.00"))

    def test_deactivate_success(self):
        result = services.deactivate_voucher(code=self.voucher.code)
        self.assertFalse(result.is_active)
        self.assertIsNotNone(result.deactivated_at)

    def test_deactivate_already_inactive(self):
        services.deactivate_voucher(code=self.voucher.code)
        with self.assertRaises(VoucherInactiveException):
            services.deactivate_voucher(code=self.voucher.code)

    def test_deactivate_nonexistent(self):
        with self.assertRaises(VoucherNotFoundException):
            services.deactivate_voucher(code="nonexistent")

    def test_deactivation_prevents_redemption(self):
        services.deactivate_voucher(code=self.voucher.code)
        with self.assertRaises(VoucherInactiveException):
            services.redeem_voucher(
                code=self.voucher.code, amount=Decimal("10.00")
            )


class VoucherModelPropertyTests(TestCase):
    """Test model property methods."""

    def test_is_usable_true(self):
        v = services.create_voucher(value=Decimal("100.00"))
        self.assertTrue(v.is_usable)

    def test_is_usable_false_when_redeemed(self):
        v = services.create_voucher(value=Decimal("10.00"))
        services.redeem_voucher(code=v.code, amount=Decimal("10.00"))
        v.refresh_from_db()
        self.assertFalse(v.is_usable)

    def test_is_usable_false_when_expired(self):
        past = timezone.now() - timezone.timedelta(hours=1)
        v = services.create_voucher(value=Decimal("10.00"), expires_at=past)
        self.assertFalse(v.is_usable)

    def test_is_expired_none_expiry(self):
        v = services.create_voucher(value=Decimal("10.00"))
        self.assertFalse(v.is_expired)

    def test_str_representation(self):
        v = services.create_voucher(value=Decimal("100.00"))
        self.assertIn("Voucher(", str(v))
        self.assertIn("100.00", str(v))
