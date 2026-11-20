# Bus-Managment-System

Enterprise-grade **Bus Ticket Management & Reservation Platform** built with **Django 5 & Django REST Framework**, engineered according to **Modular Monolith with a Service Layer** architectural pattern.

Designed with financial precision, strict domain invariants, concurrency safety, multi-leg segment-aware seat inventory, and dual-database compatibility (PostgreSQL GiST exclusion constraints & SQLite development fallback).

---

## 🏛️ Architecture & Design Principles

- **Modular Monolith:** Domain boundaries strictly separated across independent apps (`accounts`, `fleet`, `routes`, `trips`, `search`, `bookings`, `payments`, etc.) communicating through explicit Service Layers.
- **Service Layer Pattern:** Fat services, thin views, skinny models. All business logic, transaction boundaries (`transaction.atomic()`), row-level locks (`select_for_update()`), and domain invariants live inside `services.py`.
- **Financial Precision:** Authoritative banking-grade decimal calculations (`Decimal(12, 2)`) with round-to-nearest rounding (`ROUND_HALF_UP`) for gross fares, taxes, service fees, discounts, and prorated refunds.
- **Segment-Aware Inventory:** Seats are tracked along sequenced route stops $[u, v)$. Two passengers can book the *exact same physical seat* on the *same trip* if their stop intervals do not overlap.
- **Concurrency & Anti-Overselling:** Dual database support featuring PostgreSQL native `int4range` with GiST `ExclusionConstraint` on `(trip, seat, segment_range)` in production, paired with dual-dialect `SegmentRangeField` fallback on SQLite.

---

## 📊 Modules & Implementation Roadmap

| Module | Status | Highlights |
| :--- | :---: | :--- |
| **Module 0: Architecture & Base Monolith** | ✅ Complete | Django 5, DRF, dual-database setup, modular folder structure |
| **Module 1: Authentication, OTP & RBAC** | ✅ Complete | Phone OTP, 6 system roles, JWT + session auth, auto-profile |
| **Module 2: Fleet, Layouts & Seat Matrix** | ✅ Complete | 2x2, 2x1, Sleeper seat layouts, ladies-only rows, matrix generation |
| **Module 3: Terminals, Corridors & Routes** | ✅ Complete | Sequenced route stops, intermediate halt timings, distance matrix |
| **Module 4: Trips, Schedules & Pricing** | ✅ Complete | Scheduled departures, crew/bus overlap prevention, segment fare rules |
| **Module 5: Search & Availability Engine** | ✅ Complete | Corridors, station offsets, multi-leg segment overlap $(u < j \land v > i)$ |
| **Module 6: Concurrency, Locks & Bookings** | ✅ Complete | 10-min locks, PNR generation, counter agent ticketing, ladies seating rules |
| **Module 7: Payments & Reconciliation** | 🔄 Next | Pluggable gateways, idempotent webhooks, financial receipts |
| **Module 8: Tickets, QR Codes & Conductor** | 📋 Planned | Cryptographic QR codes, PDF tickets, boarding manifest scanner |
| **Module 9: Dynamic Fares & Yield Management** | 📋 Planned | Demand-based surging, early-bird discounts, holiday multipliers |
| **Module 10: Cancellations, Refunds & Credits**| 📋 Planned | Prorated tier refund policies, credit wallets |
| **Module 11: Real-time Notifications** | 📋 Planned | SMS/Email confirmations, departure alerts, schedule delays |
| **Module 12: Reporting & Analytics** | 📋 Planned | Route profitability, seat occupancy yield, conductor audit logs |
| **Module 13: Operations Dashboard & Tracking** | 📋 Planned | Live bus fleet positioning, terminal dispatch console |

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.12+ (tested on Python 3.14)
- PostgreSQL (recommended for production) or SQLite (for local development)
- Git

### 2. Installation
```powershell
# Clone the repository
git clone https://github.com/Ab5ullah/Bus-Managment-System.git
cd Bus-Managment-System

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # On Windows
# source .venv/bin/activate    # On Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```powershell
copy .env.example .env
```
Key environment variables:
```ini
DJANGO_SECRET_KEY=your-secure-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DB_ENGINE=sqlite  # Set to 'postgres' for PostgreSQL container
```

### 4. Database Migrations & Initial Setup
```powershell
python manage.py migrate
```

### 5. Run the Interactive Web Control Portal
```powershell
python manage.py runserver
```
Visit the interactive dashboard in your browser:
- **Web Control Portal:** `http://127.0.0.1:8000/`
- **Django Administration:** `http://127.0.0.1:8000/admin/`

---

## 🧪 Testing & Verification

The project includes an exhaustive automated test suite and standalone verification scripts for every completed module:

```powershell
# Run all automated pytest unit & integration tests (116 passing tests)
pytest

# Run standalone verification scripts
python verify_module_0.py
python verify_module_1.py
python verify_module_2.py
python verify_module_3.py
python verify_module_4.py
python verify_module_5.py
python verify_module_6.py
```

---

## 📡 API Reference Overview

### Authentication (`/api/accounts/`)
- `POST /api/accounts/request-otp/` — Send verification OTP
- `POST /api/accounts/verify-otp/` — Verify OTP & receive JWT token
- `GET /api/accounts/me/` — Retrieve authenticated user profile

### Fleet & Layouts (`/api/fleet/`)
- `GET /api/fleet/buses/` — List fleet with active status and seat count
- `GET /api/fleet/layouts/` — Layout matrices (2x2, 2x1, Sleeper)

### Routes & Terminals (`/api/routes/`)
- `GET /api/routes/terminals/` — City terminals and facilities
- `GET /api/routes/` — Sequenced routes with stop orders and halt durations

### Trips & Schedules (`/api/trips/`)
- `GET /api/trips/` — Active trips with bus and crew assignments
- `GET /api/trips/{id}/fares/` — Segment-specific fare matrix

### Search & Availability (`/api/search/`)
- `GET /api/search/trips/?from_stop_id={id}&to_stop_id={id}&date={YYYY-MM-DD}` — Search corridor departures
- `GET /api/search/availability/?trip_id={id}&from_stop_id={id}&to_stop_id={id}` — Segment-aware seat availability map

### Bookings & Concurrency (`/api/bookings/`)
- `POST /api/bookings/lock-seats/` — Hold up to 6 seats for 10 minutes
- `POST /api/bookings/unlock-seats/` — Early release of held seats
- `POST /api/bookings/create/` — Confirm booking from lock token (generates PNR)
- `POST /api/bookings/counter/` — Immediate walk-in counter ticket issuance
- `GET /api/bookings/{pnr}/` — Retrieve booking manifest and e-ticket
- `POST /api/bookings/{pnr}/cancel/` — Cancel seats with prorated refund calculation

---

## 🛡️ License
Proprietary / MIT License — Built by Abdullah.
