# WhatsApp Checkout Bot — WooCommerce Integration

A self-hosted FastAPI service that syncs WooCommerce products to WhatsApp Business Catalog and manages full checkout conversations entirely inside WhatsApp.

## Features

- **Catalog Sync** — Real-time + scheduled (6h) sync from WooCommerce → WhatsApp Catalog via Meta Commerce API
- **Checkout Bot** — Full conversational checkout flow with address collection, coupon validation, payment, and order confirmation
- **Shipping** — Fixed ₹30 flat rate (easily swappable)
- **Coupons** — Live validation against WooCommerce Coupons API (percentage, fixed cart, fixed product)
- **Payments** — COD or Razorpay payment links with auto-confirmation
- **Order Sync** — Creates confirmed orders in WooCommerce after checkout
- **Tracking Monitor** — Polls order notes every 15 minutes, sends tracking links via WhatsApp

## Tech Stack

- **FastAPI** + **Python 3.11+**
- **PostgreSQL** (asyncpg + SQLAlchemy async)
- **APScheduler** (catalog sync, tracking poller, session cleanup)
- **Meta Cloud API** (WhatsApp messages)
- **Meta Commerce API** (catalog sync)
- **WooCommerce REST API v3**
- **Razorpay** (payment links)

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up PostgreSQL

Create a database:
```sql
CREATE DATABASE whatsapp_bot;
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in all values.

### 4. Run database migrations

```bash
alembic upgrade head
```

Or let the app create tables on startup (dev mode).

### 5. Start the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 6. API docs

Visit `http://localhost:8000/docs` for interactive Swagger UI.

## Project Structure

```
├── main.py              # FastAPI app init
├── config.py            # pydantic BaseSettings
├── scheduler.py         # APScheduler jobs
├── routers/             # API route handlers
├── bot/                 # State machine + session manager
│   └── states/          # Individual state handlers
├── clients/             # External API clients
├── engines/             # Shipping + coupon engines
├── sync/                # Catalog sync engine
├── monitors/            # Tracking link monitor
├── db/                  # Database models + migrations
├── utils/               # Phone, HTML, signature, retry
└── templates/           # WhatsApp message template builders
```

## Webhook Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/webhooks/whatsapp` | Meta verification |
| POST | `/webhooks/whatsapp` | Inbound WhatsApp messages |
| POST | `/webhooks/woo/order` | WooCommerce new order |
| POST | `/webhooks/woo/product` | Product CRUD sync |
| POST | `/api/payments/razorpay/webhook` | Razorpay payment events |

## Database

Only **3 tables** exist locally:
- `checkout_sessions` — Bot's working memory for live conversations
- `addresses` — Saved delivery addresses by phone
- `message_logs` — WhatsApp message audit trail

Everything else (products, orders, customers, coupons) lives in WooCommerce.


venv/scripts/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

