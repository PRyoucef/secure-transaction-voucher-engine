"""
Database Schema — Secure Transaction Voucher Engine
=====================================================
Strict constraints, UUIDv4 primary keys, cryptographic voucher codes,
and PROTECT-level foreign keys for full audit trail preservation.

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

import secrets
import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def generate_voucher_code() -> str:
    """
    Generate a 32-character cryptographically secure, URL-safe voucher code.
    Uses ``secrets.token_urlsafe`` backed by the OS CSPRNG.
    """
    return secrets.token_urlsafe(24)  # 24 bytes → 32 base64url characters


class Voucher(models.Model):
    """
    Core voucher entity.

    Security invariants:
      - ``code`` is unique, indexed, and generated via CSPRNG.
      - State booleans ``is_active`` and ``is_redeemed`` are mutually guarded
        by the service layer; direct mutation outside ``services.py`` is forbidden.
      - ``on_delete=PROTECT`` on foreign keys prevents orphaned audit records.
    """

    class Meta:
        db_table = "vtx_voucher"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["code"], name="idx_voucher_code"),
            models.Index(fields=["is_active", "is_redeemed"], name="idx_voucher_state"),
            models.Index(fields=["expires_at"], name="idx_voucher_expiry"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(value__gte=0),
                name="chk_voucher_value_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(remaining_value__gte=0),
                name="chk_voucher_remaining_non_negative",
            ),
            models.CheckConstraint(
                check=~(models.Q(is_active=False) & models.Q(is_redeemed=False)
                         & models.Q(deactivated_at__isnull=True)),
                name="chk_voucher_deactivation_consistency",
            ),
        ]

    # ── Identity ──────────────────────────────────────────────────────────
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    code = models.CharField(
        max_length=64,
        unique=True,
        default=generate_voucher_code,
        db_index=True,
        editable=False,
        help_text="Cryptographically secure voucher code (CSPRNG).",
    )

    # ── Value ─────────────────────────────────────────────────────────────
    value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Original face value of the voucher.",
    )
    remaining_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Current remaining balance. Decremented atomically on redemption.",
    )

    # ── State Machine ─────────────────────────────────────────────────────
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Master switch. False = permanently disabled.",
    )
    is_redeemed = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when remaining_value reaches zero via redemption.",
    )

    # ── Temporal Bounds ───────────────────────────────────────────────────
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional hard expiration. Null = never expires.",
    )
    redeemed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of full redemption (remaining_value = 0).",
    )
    deactivated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the voucher was administratively disabled.",
    )

    # ── Ownership & Metadata ──────────────────────────────────────────────
    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="vouchers_issued",
        null=True,
        blank=True,
        help_text="User the voucher was issued to.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="vouchers_created",
        null=True,
        blank=True,
        help_text="Administrative user who created this voucher.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary key-value metadata for downstream integrations.",
    )

    def __str__(self) -> str:
        return f"Voucher({self.code[:8]}…) value={self.remaining_value}/{self.value}"

    @property
    def is_expired(self) -> bool:
        """Check temporal validity without mutating state."""
        if self.expires_at is None:
            return False
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        """Compound readiness check."""
        return self.is_active and not self.is_redeemed and not self.is_expired


class RedemptionRecord(models.Model):
    """
    Immutable audit log entry for every redemption attempt — successful or not.

    ``on_delete=PROTECT`` ensures vouchers cannot be deleted while redemption
    history exists, preserving the full audit trail.
    """

    class Meta:
        db_table = "vtx_redemption_record"
        ordering = ["-redeemed_at"]
        indexes = [
            models.Index(fields=["voucher", "redeemed_at"], name="idx_redemption_voucher_time"),
        ]

    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILED_INACTIVE = "FAILED_INACTIVE", "Failed — Voucher Inactive"
        FAILED_REDEEMED = "FAILED_REDEEMED", "Failed — Already Redeemed"
        FAILED_EXPIRED = "FAILED_EXPIRED", "Failed — Voucher Expired"
        FAILED_INSUFFICIENT = "FAILED_INSUFFICIENT", "Failed — Insufficient Value"
        FAILED_CONCURRENCY = "FAILED_CONCURRENCY", "Failed — Concurrency Conflict"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    voucher = models.ForeignKey(
        Voucher,
        on_delete=models.PROTECT,
        related_name="redemptions",
        help_text="The voucher that was targeted for redemption.",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="The amount that was (or was attempted to be) redeemed.",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        help_text="Outcome of the redemption attempt.",
    )
    redeemed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="redemptions_performed",
        null=True,
        blank=True,
        help_text="User who initiated the redemption.",
    )
    redeemed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Client IP captured at redemption time for forensic audit.",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Machine-generated note explaining the outcome.",
    )

    def __str__(self) -> str:
        return (
            f"Redemption({self.id!s:.8}) "
            f"voucher={self.voucher.code[:8]}… "
            f"amount={self.amount} "
            f"status={self.status}"
        )
