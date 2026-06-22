"""
Django Admin — Secure Transaction Voucher Engine
===================================================
Read-optimized admin interface. No inline state mutation — all changes
must go through the service layer or management commands.

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from django.contrib import admin

from .models import RedemptionRecord, Voucher


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = [
        "code_short",
        "value",
        "remaining_value",
        "is_active",
        "is_redeemed",
        "expires_at",
        "created_at",
    ]
    list_filter = ["is_active", "is_redeemed"]
    search_fields = ["code", "id"]
    readonly_fields = [
        "id",
        "code",
        "value",
        "remaining_value",
        "is_active",
        "is_redeemed",
        "created_at",
        "updated_at",
        "redeemed_at",
        "deactivated_at",
    ]
    ordering = ["-created_at"]

    def code_short(self, obj):
        return f"{obj.code[:12]}…"

    code_short.short_description = "Code"

    def has_add_permission(self, request):
        """Vouchers must be created through the API/service layer."""
        return False

    def has_change_permission(self, request, obj=None):
        """State mutations are forbidden in admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Deletion is forbidden — audit trail must be preserved."""
        return False


@admin.register(RedemptionRecord)
class RedemptionRecordAdmin(admin.ModelAdmin):
    list_display = [
        "id_short",
        "voucher_code_short",
        "amount",
        "status",
        "redeemed_at",
        "ip_address",
    ]
    list_filter = ["status"]
    search_fields = ["voucher__code", "id"]
    readonly_fields = [
        "id",
        "voucher",
        "amount",
        "status",
        "redeemed_by",
        "redeemed_at",
        "ip_address",
        "notes",
    ]
    ordering = ["-redeemed_at"]

    def id_short(self, obj):
        return str(obj.id)[:8]

    id_short.short_description = "ID"

    def voucher_code_short(self, obj):
        return f"{obj.voucher.code[:12]}…"

    voucher_code_short.short_description = "Voucher"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
