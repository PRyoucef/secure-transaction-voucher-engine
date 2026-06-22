"""
Root URL Configuration
========================
Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/vouchers/", include("voucher_engine.urls")),
]
