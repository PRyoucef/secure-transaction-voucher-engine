# Secure Transaction Voucher Engine

> **A hardened, zero-trust, atomically-locked transaction voucher system built on Django 5.1 and Django REST Framework.**

---

## Architectural Overview

The Secure Transaction Voucher Engine (STVE) is a standalone Django module engineered to mint, manage, and redeem monetary vouchers with absolute protection against concurrent attacks, double-spending, and unauthorized state mutations.

### Core Defense Mechanisms

#### 1. Race Condition Elimination — Row-Level Locking

Every state-mutating operation (redemption, deactivation) is wrapped in a two-layer concurrency shield:

```
┌─────────────────────────────────────────────────────────┐
│  transaction.atomic()                                   │
│  ┌───────────────────────────────────────────────────┐  │
│  │  SELECT ... FOR UPDATE (row-level exclusive lock) │  │
│  │  ┌─────────────────────────────────────────────┐  │  │
│  │  │  State Machine Validation                   │  │  │
│  │  │  Value Mutation                             │  │  │
│  │  │  Audit Record Creation                      │  │  │
│  │  └─────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
│  COMMIT → lock released                                 │
└─────────────────────────────────────────────────────────┘
```

- **`transaction.atomic()`** ensures the entire redemption sequence either fully commits or fully rolls back — no partial state.
- **`select_for_update()`** acquires a **PostgreSQL row-level exclusive lock** on the voucher row *before* any read-modify-write cycle. Any concurrent transaction targeting the same row will **block** until the lock is released at commit time.
- This guarantees **serialized access** to each voucher's state, completely eliminating the double-spend attack vector.

#### 2. Zero-Trust State Machine

Voucher state transitions are governed by two boolean fields (`is_active`, `is_redeemed`) that operate as a finite state machine:

```
                    ┌─────────────┐
        CREATE ───► │   ACTIVE    │ (is_active=True, is_redeemed=False)
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼                         ▼
     ┌────────────────┐      ┌─────────────────┐
     │  DEACTIVATED   │      │    REDEEMED      │
     │ (is_active=    │      │ (is_redeemed=    │
     │    False)      │      │    True)         │
     └────────────────┘      └─────────────────┘
           TERMINAL               TERMINAL
```

**Critical constraint:** All state mutations occur **exclusively** within `services.py`. Views are structurally incapable of modifying database state. This eliminates an entire category of attack surfaces where API logic inadvertently bypasses business rules.

#### 3. Cryptographic Integrity

| Element | Mechanism |
|---|---|
| **Primary Keys** | UUIDv4 — non-sequential, unpredictable, collision-resistant |
| **Voucher Codes** | 32-character tokens via `secrets.token_urlsafe(24)` — backed by the OS CSPRNG (`/dev/urandom` on Linux, `CryptGenRandom` on Windows) |
| **Code Indexing** | B-tree index + unique constraint — O(log n) lookups, no full scans |

#### 4. Immutable Audit Trail

Every redemption attempt — **successful or failed** — generates an immutable `RedemptionRecord` with:
- Voucher reference (PROTECT FK — cannot be orphaned)
- Amount attempted
- Outcome status (SUCCESS, FAILED_INACTIVE, FAILED_REDEEMED, FAILED_EXPIRED, FAILED_INSUFFICIENT, FAILED_CONCURRENCY)
- Client IP address (forensic)
- Timestamp

The Django admin interface is fully locked down — all fields are read-only, and add/change/delete permissions are disabled.

---

## Project Structure

```
Secure-Transaction-Voucher-Engine/
├── config/
│   ├── __init__.py
│   ├── settings.py          # Django settings (env-var driven)
│   ├── urls.py               # Root URL configuration
│   └── wsgi.py               # WSGI entry point
├── voucher_engine/
│   ├── __init__.py
│   ├── admin.py              # Locked-down admin interface
│   ├── apps.py               # App configuration
│   ├── exceptions.py         # Typed security exceptions
│   ├── models.py             # Database schema (Voucher, RedemptionRecord)
│   ├── serializers.py        # DRF input/output serializers
│   ├── services.py           # ★ Transactional engine (ALL business logic)
│   ├── tests.py              # Comprehensive test suite
│   ├── urls.py               # API URL routing
│   └── views.py              # Thin API views
├── manage.py
├── requirements.txt
└── README.md
```

---

## API Reference

All endpoints require authentication. Responses are JSON.

### `POST /api/vouchers/create/`

Mint a new voucher.

**Request Body:**
```json
{
  "value": "100.00",
  "issued_to": 1,
  "expires_at": "2026-12-31T23:59:59Z",
  "metadata": {"campaign": "summer-2026"}
}
```

**Response (201):**
```json
{
  "id": "a1b2c3d4-...",
  "code": "xK9mZp4rT...",
  "value": "100.00",
  "remaining_value": "100.00",
  "is_active": true,
  "is_redeemed": false,
  "is_expired": false,
  "is_usable": true,
  "created_at": "2026-06-22T20:00:00Z",
  "expires_at": "2026-12-31T23:59:59Z",
  "metadata": {"campaign": "summer-2026"}
}
```

### `POST /api/vouchers/redeem/`

Atomically redeem value from a voucher. **This is the critical path.**

**Request Body:**
```json
{
  "code": "xK9mZp4rT...",
  "amount": "25.00"
}
```

**Response (200):**
```json
{
  "id": "e5f6g7h8-...",
  "voucher_code": "xK9mZp4rT...",
  "amount": "25.00",
  "status": "SUCCESS",
  "redeemed_at": "2026-06-22T20:05:00Z",
  "ip_address": "192.168.1.42"
}
```

**Error Responses:**

| Status | Code | Meaning |
|---|---|---|
| 404 | `voucher_not_found` | Code does not exist |
| 403 | `voucher_inactive` | Voucher deactivated |
| 409 | `voucher_already_redeemed` | Double-spend attempt |
| 410 | `voucher_expired` | Past expiration window |
| 422 | `insufficient_voucher_value` | Amount exceeds balance |
| 429 | `concurrency_violation` | Row lock contention |

### `POST /api/vouchers/deactivate/`

Permanently deactivate a voucher. **Requires admin privileges.** Irreversible.

**Request Body:**
```json
{
  "code": "xK9mZp4rT..."
}
```

### `GET /api/vouchers/<code>/`

Retrieve voucher details by code. Read-only.

### `GET /api/vouchers/<code>/history/`

Return the full audit trail (all `RedemptionRecord` entries) for a voucher.

---

## Setup Instructions

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ (**required** in production for `SELECT ... FOR UPDATE`)
- pip

### Installation

```bash
# 1. Clone the repository
git clone <repository-url>
cd Secure-Transaction-Voucher-Engine

# 2. Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables (production)
export DJANGO_SECRET_KEY="your-production-secret-key"
export DJANGO_DEBUG="False"
export DB_ENGINE="django.db.backends.postgresql"
export DB_NAME="voucher_engine_db"
export DB_USER="your_db_user"
export DB_PASSWORD="your_db_password"
export DB_HOST="localhost"
export DB_PORT="5432"

# 5. Run migrations
python manage.py migrate

# 6. Create superuser (for admin access)
python manage.py createsuperuser

# 7. Run the development server
python manage.py runserver
```

### Running Tests

```bash
python manage.py test voucher_engine -v 2
```

### Production Deployment Notes

1. **Database:** PostgreSQL is **mandatory** in production. SQLite does not support `SELECT ... FOR UPDATE` and will silently degrade to no-op locking.
2. **ATOMIC_REQUESTS:** Enabled by default in settings. Every HTTP request runs inside a database transaction.
3. **Rate Limiting:** DRF throttling is configured at 20 req/min (anonymous) and 100 req/min (authenticated). Adjust in `settings.py`.
4. **Security Headers:** HSTS, SSL redirect, secure cookies, XSS filter, and content-type sniffing protection are automatically enabled when `DEBUG=False`.

---

## Security Guarantees

| Threat | Mitigation |
|---|---|
| **Double-Spending** | `SELECT ... FOR UPDATE` + `transaction.atomic()` serializes all redemption access |
| **Race Conditions** | Row-level exclusive locks block concurrent transactions |
| **State Tampering** | All mutations isolated to `services.py`; views are read-only dispatchers |
| **ID Enumeration** | UUIDv4 primary keys — non-sequential, unpredictable |
| **Code Brute-Force** | 32-char CSPRNG tokens (192 bits of entropy); rate limiting at API layer |
| **Audit Evasion** | `on_delete=PROTECT` prevents deletion of vouchers with redemption history |
| **Admin Abuse** | Django admin is fully read-only; no add/change/delete permissions |
| **Partial State Corruption** | Atomic transactions ensure all-or-nothing commits |

---

## License

Copyright (c) 2026 RANI OS Ecosystem. All Rights Reserved.

This project is made publicly available for educational and portfolio purposes. 

**THIS SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED. THE COPYRIGHT HOLDER SHALL NOT BE LIABLE FOR ANY CLAIM, DAMAGES, OR OTHER LIABILITY ARISING FROM THE USE OF THIS SOFTWARE.**
