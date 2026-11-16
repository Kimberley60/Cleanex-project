# CLEANEX

CLEANEX is a Django-based cleaning-services web application for the Kenyan market. It supports guest and registered-customer quote requests, service bookings, booking tracking, M-Pesa payments, and staff-managed quotations and notifications.

## Project overview

CLEANEX lets customers discover cleaning services, request quotes, book appointments, track bookings, and pay by M-Pesa. Staff use Django admin to manage services, bookings, quotes, payments, notifications, and website content.

The project is organised as a backend-first Django application: models define the data, service-layer functions contain the business rules, and views/templates render the UI.

## Key features

- Service catalogue with categories, features, FAQs, and extra services.
- Guest and authenticated quote requests with staff quotation workflow.
- Customer accounts with email verification, login, password reset, profile, dashboard, and quote history.
- Booking creation, session-bound confirmation, public tracking, and staff status management.
- M-Pesa Daraja STK Push payments with callback handling, independent STK Push Query verification, persistent callback diagnostics, and a shared application-wide query throttle.
- Persistent quote-status email notifications with staff-authorised retry.
- Public endpoint rate limiting for contact, quote, booking, registration, login, and password-reset forms.
- Lightweight `/health/` endpoint for deployment health checks.
- Django admin workflows for bookings, quotes, payments, notifications, services, staff, gallery, and CMS content.

## Technology stack

- Python 3.14
- Django 6.0.4
- PostgreSQL (local) / Neon PostgreSQL (deployment)
- Bootstrap 5 with custom CSS/JavaScript
- `python-decouple` for environment configuration
- `psycopg` for PostgreSQL connectivity
- Pillow for Django image fields
- `requests` for Safaricom Daraja API calls
- Cloudinary for uploaded media when configured
- WhiteNoise for production static files
- Gunicorn as the production WSGI server

## Application architecture

The codebase follows a Data → Business Logic → UI structure:

- **Data:** Django models in each app's `models.py`.
- **Business logic:** service functions in each app's `services.py`.
- **UI:** Django templates with inheritance, reusable components, and Bootstrap styling.

Multi-step writes use `transaction.atomic` and `select_for_update` where concurrency matters, such as booking status changes, quote status changes, payment transitions, customer verification, and notification delivery.

## Application/module overview

- `apps_core/core/` — home and contact pages, CMS models, contact email, public rate limiting, health check.
- `apps_core/customers/` — customer accounts, registration/verification, profile, dashboard, quote history.
- `apps_core/services/` — service categories, services, features, FAQs, and extra services.
- `apps_core/staff/` — staff records and availability/assignment logic.
- `apps_core/bookings/` — booking model, creation, tracking, status lifecycle, and admin protections.
- `apps_core/quotes/` — quote requests, estimates, staff quotations, customer accept/decline.
- `apps_core/payments/` — payment model, M-Pesa integration, callback, verification, reconciliation command.
- `apps_core/notifications/` — quote-status notification persistence, delivery, and retry.
- `apps_core/gallery/` — before/after gallery and public gallery photos.
- `apps_core/website/` — hero/process CMS models and shared website content.
- `config/` — Django settings and root URL configuration.
- `templates/` — base layout, components, pages, account, email, error, registration, and service templates.
- `static/` — CSS, JavaScript, and image assets.
- `media/` — uploaded files in development.

## Main workflows

### Customer registration and email verification

Customers register at `/account/register/`. An inactive user and pending registration are created, and a verification email is sent. The verification link activates the account, creates or links the `Customer` record by normalised email, and attaches matching guest quote requests.

### Guest and authenticated quote requests

Guests and logged-in customers can submit quotes at `/quote/`. Guest requests are stored without a customer account; authenticated requests are linked to the customer's profile. Staff review requests in admin, prepare quotations, and send them to the customer. Customers can accept or decline sent quotes from their account.

### Booking creation and tracking

Customers book services at `/booking/`. The booking form validates service, date, time, city, address, and extras, then assigns available staff. A session-bound success page confirms the booking, and `/booking/track/` lets customers look up bookings by reference and email. Staff manage booking status through admin actions that enforce allowed transitions.

### M-Pesa payment initiation and verification

Eligible bookings can be paid at `/payments/booking/<id>/initiate/`. The payment flow creates a `Payment` record, calls Safaricom Daraja STK Push, and handles the callback at `/payments/mpesa/callback/`. The callback is treated only as a trigger; the system independently queries Daraja with `query_stk_push` before marking a payment paid or failed.

Verification is deliberately conservative:

- Each payment has its own verification lease (single-flight), cooldown (`next_verification_at`), and bounded exponential backoff with jitter.
- All STK Push Query calls share an application-wide throttle (`MpesaQueryThrottle`, a database singleton) because Daraja rate-limits per API consumer, not per payment. A fixed window caps query requests across all payments and workers, and an HTTP 429 response sets a shared `blocked_until` backoff that honours `Retry-After` when present.
- Unsuccessful callbacks (non-zero `ResultCode`) are persisted as read-only `MpesaCallbackResult` diagnostics for admin review; they never change payment state.
- Paid payments with pending callback metadata (`MpesaCallbackMetadata`) are reconciled by independently re-querying Daraja before receipt/transaction-date enrichment is applied.

The management command `reconcile_mpesa_payments` can re-check unresolved payments and pending callback metadata. It respects the same per-payment lease/cooldown and the shared query throttle, and never initiates new STK Push requests:

```powershell
python manage.py reconcile_mpesa_payments --dry-run
python manage.py reconcile_mpesa_payments --limit 100
python manage.py reconcile_mpesa_payments --payment-id <payment-id>
```

### Staff quotation and booking management

Staff use Django admin to prepare quote amounts and validity dates, change quote status, manage booking status, assign staff, and review payments and notifications. Admin actions reuse the same service-layer functions as the public workflows, so business rules are not duplicated.

### Quote-status email notifications

Quote status changes create durable `QuoteStatusNotification` records with recipient, subject, body snapshots, attempt count, and delivery status. Delivery is claimed atomically, sent through the configured email backend, and can be retried by authorised staff from admin.

## Application screenshots

CLEANEX customer-facing pages and workflows:

![Homepage](screenshots/homepage.png)
*Homepage with hero section, services overview, testimonials, and FAQ.*

![Services page](screenshots/servicespage.png)
*Service catalogue with categories and pricing.*

![Service detail page](screenshots/servicesdetailpage.png)
*Service detail view with description, pricing, and booking call-to-action.*

![Gallery](screenshots/gallery.png)
*Before/after cleaning gallery with service and category filters.*

![Booking form](screenshots/bookingform.png)
*Booking form with service, date/time, location, and extras selection.*

![Booking tracking](screenshots/bookingtrack.png)
*Public booking lookup by reference and email (demo data shown).*

![Customer dashboard](screenshots/customerdashboard.png)
*Registered customer dashboard with bookings and quote history (demo data shown).*

## Local installation and setup

These instructions are for Windows PowerShell.

### 1. Clone the repository

```powershell
git clone https://github.com/Kimberley60/Cleanex-project.git
cd Cleanex-project
```

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script execution, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 3. Install dependencies

Install the pinned direct dependencies from `requirements.txt`:

```powershell
pip install -r requirements.txt
```

### 4. Create `.env`

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set the required values. `.env.example` documents Django/security, database, email, M-Pesa, and Cloudinary variable names; keep real secrets out of version control.

### 5. Configure PostgreSQL

For local development, the project expects a PostgreSQL database named `cleanex_db` and user `cleanex_user` on `localhost:5432` by default. Create them in PostgreSQL, for example:

```sql
CREATE USER cleanex_user WITH PASSWORD 'your-database-password';
CREATE DATABASE cleanex_db OWNER cleanex_user;
```

Set the same password in `.env` as `DB_PASSWORD`. For Neon or another hosted PostgreSQL provider, set `DATABASE_URL` instead and leave the local `DB_*` values unused.

### 6. Run migrations

```powershell
python manage.py migrate
```

### 7. Create a superuser

```powershell
python manage.py createsuperuser
```

### 8. Start the development server

```powershell
python manage.py runserver
```

Then open `http://127.0.0.1:8000/`.

## Environment variables

Configuration is read with `python-decouple`. Do not commit `.env` or expose real credentials.

### Django/security

- `SECRET_KEY` — Django secret key. A local-development fallback exists, but `DEBUG=False` requires a strong environment value.
- `DEBUG` — `True` for local development, `False` for production-like environments.
- `ALLOWED_HOSTS` — comma-separated hostnames allowed to serve the site.
- `CSRF_TRUSTED_ORIGINS` — comma-separated HTTPS origins for CSRF when needed behind a proxy/custom domain.
- `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_REFERRER_POLICY` — HTTPS hardening applied when `DEBUG=False`.

### Database

- `DATABASE_URL` — full PostgreSQL connection URL for Neon/Render, for example `postgresql://user:password@host/dbname?sslmode=require`.
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` — local PostgreSQL settings used when `DATABASE_URL` is empty.

### Email

- `EMAIL_BACKEND` — Django email backend.
- `EMAIL_HOST` — SMTP host.
- `EMAIL_PORT` — SMTP port.
- `EMAIL_HOST_USER` — SMTP username.
- `EMAIL_HOST_PASSWORD` — SMTP password or app password.
- `EMAIL_USE_TLS` — enable TLS.
- `EMAIL_USE_SSL` — enable SSL.
- `EMAIL_TIMEOUT` — SMTP timeout in seconds.
- `DEFAULT_FROM_EMAIL` — default sender address.
- `CONTACT_RECIPIENT_EMAIL` — recipient for contact-form messages.

### M-Pesa Daraja

- `MPESA_CONSUMER_KEY` — Daraja consumer key.
- `MPESA_CONSUMER_SECRET` — Daraja consumer secret.
- `MPESA_SHORTCODE` — Safaricom shortcode.
- `MPESA_PASSKEY` — Lipa na M-Pesa passkey.
- `MPESA_CALLBACK_URL` — public HTTPS callback URL for STK Push results.
- `MPESA_ENVIRONMENT` — `sandbox` or `production`.

Keep `MPESA_ENVIRONMENT=sandbox` for local development and portfolio demonstrations unless you deliberately configure production credentials and a production callback URL.

### Cloudinary media storage

- `CLOUDINARY_CLOUD_NAME` — Cloudinary cloud name.
- `CLOUDINARY_API_KEY` — Cloudinary API key.
- `CLOUDINARY_API_SECRET` — Cloudinary API secret.

If all three Cloudinary variables are set, uploaded media uses `apps_core.core.storage.CloudinaryMediaStorage`; otherwise Django falls back to local `FileSystemStorage` under `MEDIA_ROOT`.

## Database setup and migrations

After configuring PostgreSQL and `.env`, run:

```powershell
python manage.py migrate
python manage.py createsuperuser
```

To check that models and migrations are in sync without changing the database:

```powershell
python manage.py makemigrations --check --dry-run
```

## Running tests

Full suite:

```powershell
.\.venv\Scripts\python.exe manage.py test --keepdb -v 1
```

Latest verified baseline:

- Verified full-suite baseline: 599 tests passed (`manage.py test -v 1`, 2026-11-16); `manage.py check` and `makemigrations --check --dry-run` clean. `apps_core.payments` alone: 204 tests passed (2026-11-16).

Automated tests mock external email and Safaricom HTTP interactions; they do not make real payment requests.

## Deployment status

Deployment **preparation and configuration documentation are complete**, and the application has been **tested locally** (599 automated tests passing as of 2026-11-16). The project has **not yet been deployed to a live hosting environment** — there is no live demo URL.

Remaining deployment steps, all future work:

- Provision a Render web service and a Neon PostgreSQL database.
- Configure environment variables in the hosting dashboard (names documented in `.env.example`; never commit real values).
- Run migrations and create a superuser in the deployed environment.
- Verify HTTPS, email delivery, M-Pesa sandbox callback reachability, and Cloudinary media in production-like conditions.

The configuration below documents the intended Render + Neon + Cloudinary setup.

## Deployment on Render with Neon and Cloudinary

The project is configured for a Render Django web service, Neon PostgreSQL, and Cloudinary media storage. Do not commit `.env`; set the same variable names from `.env.example` in the Render dashboard.

Recommended Render settings:

- **Build command:** `pip install -r requirements.txt && python manage.py collectstatic --no-input`
- **Start command:** `gunicorn config.wsgi:application`
- **Health check path:** `/health/`
- **Environment:** set `DEBUG=False`, a strong `SECRET_KEY`, `ALLOWED_HOSTS` including the Render hostname/custom domain, `CSRF_TRUSTED_ORIGINS=https://<your-domain>`, and `DATABASE_URL` from Neon.

Database and media notes:

- Neon: paste the Neon connection string into `DATABASE_URL`; the settings parser preserves `sslmode` from the URL query string.
- Cloudinary: set all three `CLOUDINARY_*` variables so uploaded media is delivered by Cloudinary. Static files are collected into `staticfiles/` and served by WhiteNoise.
- M-Pesa: set `MPESA_CALLBACK_URL=https://<your-domain>/payments/mpesa/callback/` and keep `MPESA_ENVIRONMENT=sandbox` for portfolio testing.
- Reconciliation: run `python manage.py reconcile_mpesa_payments --limit 100 --delay 1` manually or schedule it externally/with a Render Cron Job; there is no built-in task queue.

Email limitation: Render free web services block outbound SMTP ports 25/465/587. Gmail/SMTP credentials may boot the app but cannot deliver mail on a free Render instance; use a paid Render instance or an HTTPS email API/provider after a hosting decision.

## M-Pesa sandbox information

- Use `MPESA_ENVIRONMENT=sandbox` for local testing.
- Provide sandbox `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`, and `MPESA_PASSKEY` from the Safaricom Daraja portal.
- `MPESA_CALLBACK_URL` must be a publicly reachable HTTPS URL for real sandbox callbacks. For local development, use a tunnel or test the callback/verification logic through the automated tests.
- The callback endpoint only triggers verification; payment status is confirmed by an independent STK Push Query.

## Security and engineering considerations

- Business logic lives in service-layer functions rather than templates or views.
- Booking and quote status changes use explicit transition rules and row locking.
- Booking success pages and payment initiation are bound to the session that created the booking.
- Booking and extra-service history is protected with `PROTECT` relationships and name/price snapshots.
- Customer emails are normalised and uniquely constrained; guest quotes can be linked after verification.
- M-Pesa callbacks are not trusted as proof of payment; the system verifies with Daraja directly and only applies definitive query results.
- Callback diagnostics and payment verification scheduling are persisted, so restarts and multiple workers cannot duplicate provider queries or lose rejection reasons.
- The shared M-Pesa query throttle coordinates rate limiting across workers through the database rather than process-local memory.
- Quote-status notifications are persisted before delivery and can be retried by authorised staff.
- Public POST endpoints use rate limiting with normalised identifiers and digest-based cache keys.
- Admin delete protections preserve booking, payment, and notification history.

## Known limitations / future improvements

- Rate limiting uses Django's default local-memory cache, which is not shared across multiple workers or processes; use a shared cache such as Redis or Memcached for production deployments.
- `reconcile_mpesa_payments` must be run manually or scheduled externally.
- Each M-Pesa query fetches a fresh OAuth access token (no token caching), so every verification costs two Daraja requests; the shared throttle bounds this but token caching would reduce request volume further.
- The shared query throttle uses conservative constants (10 queries per 60-second window, 60-second default block); tune them if production volume requires.
- Stale notification records in `sending` state after a process crash require manual reconciliation.
- Email delivery depends on the configured SMTP provider; Render free instances block outbound SMTP ports, so a paid instance or HTTPS email API may be required.
- If Cloudinary credentials are absent, uploaded media falls back to local `FileSystemStorage`, which is not durable on ephemeral hosting.
- Test coverage is strong for core workflows but lighter for some CMS/admin areas; no coverage percentage has been established.
- Screenshots cover the main customer-facing pages; admin workflows are not pictured.

## Author/contact

GitHub: https://github.com/Kimberley60/Cleanex-project
