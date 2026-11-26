# Bus Ticket Management System (`busms`)

Enterprise-grade **Bus Ticket Management & Reservation Platform** built with **Django & Django REST Framework**, engineered according to the **Modular Monolith with a Service Layer** architectural pattern.

Designed with banking-grade financial precision, strict domain invariants, concurrency safety, multi-leg segment-aware seat inventory, dynamic gender seating rules, Stripe Checkout integration, Celery background tasks, and full audit logging.

---

## Architecture & Design Principles

- **Modular Monolith:** Domain boundaries separated across dedicated apps (`accounts`, `fleet`, `routes`, `trips`, `search`, `bookings`, `payments`, `tickets`, `cancellations`, `promotions`, `reports`, `notifications`, `audit`) communicating through explicit Service Layers.
- **Service Layer Pattern:** Fat services, thin views, skinny models. All business logic, transaction boundaries (`transaction.atomic()`), row-level locks (`select_for_update()`), and domain invariants live inside `services.py`.
- **Financial Precision:** Banking-grade `Decimal(12, 2)` calculations with `ROUND_HALF_UP` for gross fares, taxes, service fees, coupon discounts, and prorated refunds.
- **Segment-Aware Inventory:** Seats are tracked along sequenced route stops $[u, v)$. Multiple passengers can book the *same physical seat* on the *same trip* across non-overlapping legs ($u < j \land v > i$).
- **Dynamic Gender Seating & Adjacent-Female Guard:** Seats default to open seating (`ladies_rows=0`). When a female passenger books or locks a seat, the adjacent seat in the same row/aisle pair is automatically restricted to female passengers for new bookings, while still allowing mixed-gender selections within a single reservation.
- **Concurrency & Anti-Overselling:** PostgreSQL native `int4range` with GiST `ExclusionConstraint` on `(trip, seat, segment_range)` in production, paired with dual-dialect `SegmentRangeField` fallback on SQLite.

---

## Modules & Implementation Status

All **14 Modules (0–13)** are **100% complete** and verified with **208 passing automated tests**.

| Module | Status | Highlights |
| :--- | :---: | :--- |
| **Module 0: Project Setup & Base Scaffolding** | Complete | Django, DRF, dual-DB setup, money precision, Swagger/OpenAPI, health & mobile config |
| **Module 1: Accounts, Auth & RBAC** | Complete | Phone-based login, OTP verification, JWT + blacklist, lockout security, 5 roles |
| **Module 2: Fleet & Seat Layouts** | Complete | `2x2`, `2x1`, `1x1`, Sleeper layouts, open seating default, drivers & conductors |
| **Module 3: Terminals & Sequenced Routes** | Complete | Terminals, ordered route stops, intermediate halt offsets, distance matrix |
| **Module 4: Trips, Fares & Overlap Prevention** | Complete | Bus/driver schedule overlap guard, segment fare rules, trip seat blocks, live `available_seats` |
| **Module 5: Search & Availability Engine** | Complete | Corridor search (`origin`/`destination` & `origin_terminal`/`destination_terminal`), pagination, segment seat map |
| **Module 6: Seat Locking & Booking Engine** | Complete | 10-min seat locks, staff lock override & self-lock renewal, dynamic gender guard, walk-in counter ticketing |
| **Module 7: Payments, Stripe & Webhooks** | Complete | Stripe Hosted Checkout & PaymentIntents, `verify-stripe-session`, signed webhooks, refunds |
| **Module 8: Tickets, QR Codes & Conductor** | Complete | Signed QR tokens, ReportLab PDF boarding passes, gate scan & passenger manifest |
| **Module 9: Cancellation & Refund Engine** | Complete | Tiered refund policies, prorated coupon/tax accounting, partial seat cancellation, bulk trip cancellation |
| **Module 10: Promotions & Dynamic Pricing** | Complete | Server-side coupon engine, usage quotas, occupancy surge, early-bird & weekend pricing |
| **Module 11: Reports & Analytics Engine** | Complete | Revenue metrics, Load Factor (Pax-km / Seat-km), route performance, agent 5% commission, CSV/Excel export |
| **Module 12: Notifications & Celery Tasks** | Complete | Async Email/SMS `NotificationLog`, T-2h departure reminders, expired lock cleanup beat |
| **Module 13: Hardening, Audit & E2E Tests** | Complete | `AuditLog` middleware with secret masking, DRF rate throttling (`60/min` anon, `300/min` user), E2E journey test |

---

## Quickstart & Setup

### 1. Prerequisites
- Python 3.11+ (tested on Python 3.14)
- PostgreSQL (recommended for production) or SQLite (for local development)
- Redis (optional for async Celery workers; `CELERY_TASK_ALWAYS_EAGER=True` works locally without Redis)
- Git

### 2. Installation
```powershell
# Clone the repository
git clone https://github.com/Ab5ullah/Bus-Managment-System.git
cd Bus-Managment-System

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # On Windows PowerShell
# source .venv/bin/activate    # On Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
Key environment variables:
```ini
SECRET_KEY=your-secure-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DB_ENGINE=sqlite3  # Set to 'postgresql' for PostgreSQL

# Stripe Payments (optional; defaults to 'mock' if unset)
DEFAULT_PAYMENT_GATEWAY=stripe
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Celery / Redis
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_TASK_ALWAYS_EAGER=True
```

### 4. Database Migrations, Seed Data & Super Admin
```powershell
python manage.py migrate

# Optional: Seed sample terminals, buses, routes, and trips
python manage.py seed_demo_data

# Create your Super Admin (uses phone number as primary login identifier)
python manage.py createsuperuser
```

### 5. Run the Server
```powershell
python manage.py runserver
```
- **Interactive Web Portal:** `http://127.0.0.1:8000/`
- **Django Admin Panel:** `http://127.0.0.1:8000/admin/`
- **Swagger UI:** `http://127.0.0.1:8000/api/docs/`
- **ReDoc:** `http://127.0.0.1:8000/api/redoc/`

*(Optional for local Stripe webhook testing:)*
```powershell
stripe listen --forward-to http://127.0.0.1:8000/api/payments/webhook/stripe/
```

---

## Testing & Verification

Run the full automated `pytest` suite (**208 passing tests** across all modules, including the full end-to-end passenger journey test):

```powershell
pytest -q
```

Import [`busms_postman_collection.json`](busms_postman_collection.json) into **Postman** for ready-to-run requests covering Modules 0–13.

---

## Key API Endpoints

| Area | Endpoint | Purpose |
| :--- | :--- | :--- |
| **Auth** | `POST /api/auth/register/`, `verify-otp/`, `login/` | Registration, OTP verification, JWT tokens |
| **Fleet** | `GET /api/fleet/buses/`, `POST /api/fleet/buses/` | Fleet management & automatic seat matrix generation |
| **Routes** | `GET /api/routes/terminals/`, `GET /api/routes/` | Terminals & sequenced intermediate stops |
| **Trips** | `GET /api/trips/`, `POST /api/trips/` | Scheduled trips, crew overlap validation & `available_seats` |
| **Search** | `GET /api/search/trips/?origin=...&destination=...` | Corridor search (supports `origin_terminal` / `destination_terminal` & pagination) |
| **Seat Map** | `GET /api/search/trips/{id}/seat-map/?from_stop=&to_stop=` | Real-time availability + `female_adjacent_restricted` & `allowed_genders` |
| **Bookings** | `POST /api/bookings/lock-seats/`, `create/`, `counter/` | 10-min holds, PNR confirmation & walk-in counter ticketing |
| **Payments** | `POST /api/payments/initiate/`, `verify-stripe-session/`, `webhook/<gateway>/` | Stripe Checkout / PaymentIntent, verification & webhooks |
| **Tickets** | `GET /api/tickets/booking/{pnr}/`, `{ticket}/pdf/`, `POST /api/tickets/board/` | PDF e-tickets, QR verification & conductor boarding |
| **Cancellations** | `POST /api/cancellations/cancel-booking/`, `cancel-seats/` | Tiered refund policy & partial seat release |
| **Promotions** | `POST /api/promotions/apply-coupon/`, `GET /api/promotions/dynamic-price/` | Coupons & dynamic fare rules |
| **Reports** | `GET /api/admin/reports/revenue/`, `occupancy/`, `agent-sales/`, `export/` | BI analytics & CSV / Excel `.xlsx` exports |
| **Notifications** | `GET /api/admin/notifications/logs/` | Email/SMS dispatch logs & housekeeping triggers |
| **Audit** | `GET /api/admin/audit/logs/` | Immutable API audit logs with masked secrets |

---

## License
Proprietary / MIT License — Built by Abdullah.
