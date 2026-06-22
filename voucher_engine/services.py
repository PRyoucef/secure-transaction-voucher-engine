"""
Transactional Service Layer — Secure Transaction Voucher Engine
=================================================================
ALL business logic and state mutations live here.
Views are strictly forbidden from performing database writes directly.

Race Condition Elimination Strategy:
  1. ``transaction.atomic()`` wraps every state-mutating operation.
  2. ``select_for_update()`` acquires a row-level exclusive lock BEFORE
     any read-modify-write cycle, guaranteeing serialized access.
  3. Every operation records a ``RedemptionRecord`` — success or failure —
     for complete forensic auditability.

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.db import OperationalError, transaction
from django.utils import timezone

from .exceptions import (
    ConcurrencyViolationException,
    InsufficientVoucherValueException,
    VoucherAlreadyRedeemedException,
    VoucherCreationException,
    VoucherExpiredException,
    VoucherInactiveException,
    VoucherNotFoundException,
)
from .models import RedemptionRecord, Voucher

logger = logging.getLogger("voucher_engine.services")


# ═══════════════════════════════════════════════════════════════════════════
#  VOUCHER CREATION
# ═══════════════════════════════════════════════════════════════════════════


def create_voucher(
    *,
    value: Decimal,
    issued_to_id: Optional[int] = None,
    created_by_id: Optional[int] = None,
    expires_at=None,
    metadata: Optional[dict] = None,
) -> Voucher:
    """
    Mint a new voucher with cryptographically generated code.

    Args:
        value: Face value (must be > 0).
        issued_to_id: PK of the user receiving the voucher.
        created_by_id: PK of the admin creating the voucher.
        expires_at: Optional expiration datetime (timezone-aware).
        metadata: Arbitrary key-value pairs.

    Returns:
        The persisted ``Voucher`` instance.

    Raises:
        VoucherCreationException: If value ≤ 0 or persistence fails.
    """
    if value <= 0:
        raise VoucherCreationException(
            detail=f"Voucher value must be positive. Received: {value}"
        )

    try:
        with transaction.atomic():
            voucher = Voucher.objects.create(
                value=value,
                remaining_value=value,
                issued_to_id=issued_to_id,
                created_by_id=created_by_id,
                expires_at=expires_at,
                metadata=metadata or {},
            )
    except Exception as exc:
        logger.exception("Voucher creation failed: %s", exc)
        raise VoucherCreationException(
            detail="An internal error prevented voucher creation."
        ) from exc

    logger.info(
        "Voucher created: id=%s code=%s value=%s",
        voucher.id,
        voucher.code[:8],
        voucher.value,
    )
    return voucher


# ═══════════════════════════════════════════════════════════════════════════
#  VOUCHER RETRIEVAL (Read-Only)
# ═══════════════════════════════════════════════════════════════════════════


def get_voucher_by_code(code: str) -> Voucher:
    """
    Retrieve a voucher by its code. Does NOT acquire a lock.

    Raises:
        VoucherNotFoundException: If no voucher matches the code.
    """
    try:
        return Voucher.objects.get(code=code)
    except Voucher.DoesNotExist:
        raise VoucherNotFoundException()


def get_voucher_by_id(voucher_id) -> Voucher:
    """
    Retrieve a voucher by its UUID primary key. Does NOT acquire a lock.

    Raises:
        VoucherNotFoundException: If no voucher matches the ID.
    """
    try:
        return Voucher.objects.get(pk=voucher_id)
    except Voucher.DoesNotExist:
        raise VoucherNotFoundException()


# ═══════════════════════════════════════════════════════════════════════════
#  VOUCHER REDEMPTION — Critical Transaction Path
# ═══════════════════════════════════════════════════════════════════════════


def redeem_voucher(
    *,
    code: str,
    amount: Decimal,
    redeemed_by_id: Optional[int] = None,
    ip_address: Optional[str] = None,
) -> RedemptionRecord:
    """
    Atomically redeem ``amount`` from the voucher identified by ``code``.

    Concurrency Model:
      1. Open an ``ATOMIC`` transaction.
      2. ``SELECT … FOR UPDATE`` locks the voucher row — any concurrent
         transaction attempting the same lock will BLOCK until this one commits.
      3. Validate the state machine (active → not redeemed → not expired → sufficient funds).
      4. Mutate ``remaining_value``; if it hits zero, flip ``is_redeemed`` and stamp ``redeemed_at``.
      5. Write a ``RedemptionRecord`` with status ``SUCCESS``.
      6. Commit — the row lock is released.

    On any validation failure, a ``RedemptionRecord`` with the appropriate
    ``FAILED_*`` status is persisted in a **separate** atomic block so that
    the audit trail survives even when the primary transaction rolls back.

    Args:
        code: The voucher code to redeem against.
        amount: The monetary amount to deduct.
        redeemed_by_id: PK of the redeeming user (optional).
        ip_address: Client IP for forensic logging (optional).

    Returns:
        A ``RedemptionRecord`` with status ``SUCCESS``.

    Raises:
        VoucherNotFoundException: Code does not resolve.
        VoucherInactiveException: Voucher has been deactivated.
        VoucherAlreadyRedeemedException: Voucher fully consumed.
        VoucherExpiredException: Voucher past its expiration window.
        InsufficientVoucherValueException: Amount exceeds remaining balance.
        ConcurrencyViolationException: Row lock could not be acquired (NOWAIT variant).
    """
    failure_context: Optional[dict] = None

    try:
        with transaction.atomic():
            # ── Step 1: Acquire exclusive row lock ────────────────────────
            try:
                voucher = (
                    Voucher.objects
                    .select_for_update(nowait=False)
                    .get(code=code)
                )
            except Voucher.DoesNotExist:
                raise VoucherNotFoundException()

            # ── Step 2: State machine validation ──────────────────────────
            failure_status = _validate_voucher_state(voucher, amount)
            if failure_status is not None:
                # Capture context for audit logging OUTSIDE this atomic block
                failure_context = {
                    "voucher_id": voucher.pk,
                    "amount": amount,
                    "status": failure_status,
                    "redeemed_by_id": redeemed_by_id,
                    "ip_address": ip_address,
                }
                _raise_for_failure_status(failure_status)

            # ── Step 3: Atomic value mutation ─────────────────────────────
            voucher.remaining_value -= amount

            if voucher.remaining_value == Decimal("0.00"):
                voucher.is_redeemed = True
                voucher.redeemed_at = timezone.now()

            voucher.save(
                update_fields=[
                    "remaining_value",
                    "is_redeemed",
                    "redeemed_at",
                    "updated_at",
                ]
            )

            # ── Step 4: Audit trail ───────────────────────────────────────
            record = RedemptionRecord.objects.create(
                voucher=voucher,
                amount=amount,
                status=RedemptionRecord.Status.SUCCESS,
                redeemed_by_id=redeemed_by_id,
                ip_address=ip_address,
                notes=f"Redeemed {amount}. Remaining: {voucher.remaining_value}",
            )

    except (
        VoucherInactiveException,
        VoucherAlreadyRedeemedException,
        VoucherExpiredException,
        InsufficientVoucherValueException,
    ) as domain_exc:
        # ── Persist failed audit record in a SEPARATE transaction ─────
        # The primary atomic block has rolled back at this point.
        # We open a new atomic block to ensure the failure is recorded.
        if failure_context is not None:
            with transaction.atomic():
                _record_failed_redemption(
                    voucher_id=failure_context["voucher_id"],
                    amount=failure_context["amount"],
                    status=failure_context["status"],
                    redeemed_by_id=failure_context["redeemed_by_id"],
                    ip_address=failure_context["ip_address"],
                )
        raise domain_exc
    except VoucherNotFoundException:
        raise
    except OperationalError as exc:
        logger.error("Row lock contention on voucher code=%s: %s", code[:8], exc)
        raise ConcurrencyViolationException() from exc
    except Exception as exc:
        logger.exception("Unexpected error during redemption of code=%s: %s", code[:8], exc)
        raise

    logger.info(
        "Redemption successful: voucher=%s amount=%s remaining=%s",
        voucher.code[:8],
        amount,
        voucher.remaining_value,
    )
    return record


# ═══════════════════════════════════════════════════════════════════════════
#  VOUCHER DEACTIVATION
# ═══════════════════════════════════════════════════════════════════════════


def deactivate_voucher(*, code: str) -> Voucher:
    """
    Administratively deactivate a voucher. Irreversible.

    Acquires a row lock to prevent concurrent redemption during deactivation.

    Raises:
        VoucherNotFoundException: If the voucher does not exist.
        VoucherInactiveException: If the voucher is already deactivated.
    """
    with transaction.atomic():
        try:
            voucher = (
                Voucher.objects
                .select_for_update()
                .get(code=code)
            )
        except Voucher.DoesNotExist:
            raise VoucherNotFoundException()

        if not voucher.is_active:
            raise VoucherInactiveException(
                detail="Voucher is already deactivated."
            )

        voucher.is_active = False
        voucher.deactivated_at = timezone.now()
        voucher.save(update_fields=["is_active", "deactivated_at", "updated_at"])

    logger.info("Voucher deactivated: code=%s", voucher.code[:8])
    return voucher


# ═══════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _validate_voucher_state(
    voucher: Voucher, amount: Decimal
) -> Optional[str]:
    """
    Run the state machine gauntlet. Returns the failure status string
    or ``None`` if all checks pass.
    """
    if not voucher.is_active:
        return RedemptionRecord.Status.FAILED_INACTIVE

    if voucher.is_redeemed:
        return RedemptionRecord.Status.FAILED_REDEEMED

    if voucher.is_expired:
        return RedemptionRecord.Status.FAILED_EXPIRED

    if amount > voucher.remaining_value:
        return RedemptionRecord.Status.FAILED_INSUFFICIENT

    return None


def _record_failed_redemption(
    *,
    voucher_id,
    amount: Decimal,
    status: str,
    redeemed_by_id: Optional[int],
    ip_address: Optional[str],
) -> RedemptionRecord:
    """Persist a failed redemption record in its own atomic block."""
    return RedemptionRecord.objects.create(
        voucher_id=voucher_id,
        amount=amount,
        status=status,
        redeemed_by_id=redeemed_by_id,
        ip_address=ip_address,
        notes=f"Rejected: {status}",
    )


def _raise_for_failure_status(status: str) -> None:
    """Map a failure status to its corresponding domain exception."""
    exception_map = {
        RedemptionRecord.Status.FAILED_INACTIVE: VoucherInactiveException,
        RedemptionRecord.Status.FAILED_REDEEMED: VoucherAlreadyRedeemedException,
        RedemptionRecord.Status.FAILED_EXPIRED: VoucherExpiredException,
        RedemptionRecord.Status.FAILED_INSUFFICIENT: InsufficientVoucherValueException,
    }
    exc_class = exception_map.get(status)
    if exc_class:
        raise exc_class()
