"""
URL Configuration — Secure Transaction Voucher Engine
=======================================================
Mount these under your project's root ``urlpatterns``:

    path("api/vouchers/", include("voucher_engine.urls")),

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from django.urls import path

from . import views

app_name = "voucher_engine"

urlpatterns = [
    path(
        "create/",
        views.VoucherCreateView.as_view(),
        name="voucher-create",
    ),
    path(
        "redeem/",
        views.VoucherRedeemView.as_view(),
        name="voucher-redeem",
    ),
    path(
        "deactivate/",
        views.VoucherDeactivateView.as_view(),
        name="voucher-deactivate",
    ),
    path(
        "<str:code>/",
        views.VoucherDetailView.as_view(),
        name="voucher-detail",
    ),
    path(
        "<str:code>/history/",
        views.VoucherRedemptionHistoryView.as_view(),
        name="voucher-history",
    ),
]
