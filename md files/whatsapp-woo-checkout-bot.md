# WhatsApp Checkout Bot — WooCommerce Integration
### Full Project Specification

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Tech Stack](#3-tech-stack)
4. [System Components](#4-system-components)
5. [Database Schema](#5-database-schema)
6. [Catalog Sync Engine](#6-catalog-sync-engine)
7. [Conversation State Machine](#7-conversation-state-machine)
8. [Full Checkout Flow](#8-full-checkout-flow)
9. [Shipping Engine](#9-shipping-engine)
10. [Discounts & Coupons](#10-discounts--coupons)
11. [Payment Handling](#11-payment-handling)
12. [Order Sync to WooCommerce](#12-order-sync-to-woocommerce)
13. [Tracking Link Monitor](#13-tracking-link-monitor)
14. [API Endpoints](#14-api-endpoints)
15. [Webhook Flows](#15-webhook-flows)
16. [Message Templates](#16-message-templates)
17. [Config & Environment Variables](#17-config--environment-variables)
18. [Error Handling & Edge Cases](#18-error-handling--edge-cases)
19. [Folder Structure](#19-folder-structure)
20. [Build Phases & Effort Estimate](#20-build-phases--effort-estimate)

---

## 1. Project Overview

### What This System Does

A self-hosted FastAPI service that:

- **Syncs** WooCommerce products and categories to the WhatsApp Business Catalog (via Meta Commerce API) in real-time and on schedule
- **Intercepts** the post-order WhatsApp notification triggered by WooCommerce and **takes over** the conversation to run a full checkout flow entirely inside WhatsApp
- **Manages** address collection with saved-address memory per phone number
- **Calculates** shipping at a fixed rate of ₹30 per order (easily swappable later)
- **Applies** discounts and coupon codes validated live against WooCommerce
- **Collects** payment via COD or a Razorpay payment link
- **Creates** a confirmed order back in WooCommerce once the user confirms
- **Monitors** WooCommerce order notes for tracking links and automatically pushes them to the customer on WhatsApp

### What This System Is NOT

- It is not a storefront replacement — browsing and cart happen natively in WhatsApp Catalog
- It does not replace WooCommerce — WooCommerce is the **single source of truth** for all products, orders, customers, inventory, and coupons
- It is not a BSP-dependent system — it talks directly to Meta Cloud API

### Data Ownership Principle

> **WooCommerce owns all business data. The local database owns only what WooCommerce cannot store: live conversation state, saved delivery addresses, and WhatsApp message logs.**

Products, orders, customers, coupons — all queried live from WooCommerce. Nothing is mirrored locally.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        WOOCOMMERCE                              │
│  Products · Orders · Customers · Coupons · Order Notes/Meta     │
│                  ← Single Source of Truth →                     │
└────────┬────────────────────────────────────────┬───────────────┘
         │ REST API (pull on demand)               │ Webhooks (push)
         │                                         │ order.created
         │                                         │ product.created/updated/deleted
         ▼                                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FASTAPI SYNC & BOT SERVICE                   │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐  ┌─────────────┐  │
│  │  Catalog Sync    │   │  Checkout Bot    │  │  Tracking   │  │
│  │  Engine          │   │  (State Machine) │  │  Monitor    │  │
│  └──────────────────┘   └──────────────────┘  └─────────────┘  │
│                                                                 │
│  ┌──────────────────┐   ┌──────────────────┐  ┌─────────────┐  │
│  │  Shipping Engine │   │  Payment Handler │  │  Coupon     │  │
│  │  (Fixed ₹30)     │   │  (COD/Razorpay)  │  │  Engine     │  │
│  └──────────────────┘   └──────────────────┘  └─────────────┘  │
│                                                                 │
│  ┌──────────────────────┐   ┌──────────────────────────────┐   │
│  │  PostgreSQL (lean)   │   │  APScheduler (Cron Jobs)     │   │
│  │  · checkout_sessions │   │  · Full catalog sync (6h)    │   │
│  │  · addresses         │   │  · Tracking poller (15min)   │   │
│  │  · message_logs      │   │  · Session expiry cleanup    │   │
│  └──────────────────────┘   └──────────────────────────────┘   │
└──────────┬──────────────────────────────┬───────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐      ┌────────────────────────┐
│  META COMMERCE API  │      │  META CLOUD API         │
│  (Catalog Sync)     │      │  (WhatsApp Messages)    │
└─────────────────────┘      └────────────────────────┘
                                          │
                                          ▼
                               ┌────────────────────┐
                               │  END USER           │
                               │  (WhatsApp)         │
                               └────────────────────┘
```

---

## 3. Tech Stack

| Layer | Technology |
|---|---|
| Backend Framework | FastAPI (Python 3.11+) |
| Database | PostgreSQL (via asyncpg / SQLAlchemy async) |
| Task Scheduler | APScheduler (AsyncIO mode) |
| WhatsApp API | Meta Cloud API (self-managed, direct) |
| Catalog API | Meta Commerce API (Graph API v19+) |
| WooCommerce | WooCommerce REST API v3 |
| Payment | Razorpay (payment links) |
| HTTP Client | httpx (async) |
| Deployment | VPS + aaPanel + Nginx + Uvicorn |
| Environment | python-dotenv / pydantic BaseSettings |

---

## 4. System Components

### 4.1 Catalog Sync Engine
Keeps the WhatsApp Business Catalog in sync with WooCommerce products and categories. Pulls from WooCommerce REST API — no local product mirror. Runs on webhook triggers (instant) and a scheduled full-sync every 6 hours as a safety net.

### 4.2 Checkout Bot (State Machine)
The core of the project. A per-user state machine that manages the full checkout conversation. State is persisted in PostgreSQL so the bot survives restarts. All product and order data is fetched live from WooCommerce — the state machine only stores transient session data (current step, cart snapshot, payment references).

### 4.3 Shipping Engine
Currently a fixed ₹30 flat rate applied to every order. Resolved through a single `calculate_shipping()` function so switching to zone/weight-based logic later requires changing only that one file.

### 4.4 Coupon Engine
Validates coupon codes live against the WooCommerce Coupons API. Checks all Woo-native constraints (expiry, usage limits, minimum spend, product restrictions). Calculates discounts of type: percentage, fixed cart, and fixed product.

### 4.5 Payment Handler
Generates Razorpay payment links for online orders and handles the async payment webhook callback. COD orders are marked directly with no external call.

### 4.6 Order Sync
After user confirmation, creates a fully-formed WooCommerce order via the REST API. WooCommerce is the canonical record from this point. The tracking prevention flag (`_wa_tracking_sent`) is stored as WooCommerce order meta — no local orders table needed.

### 4.7 Tracking Monitor
APScheduler job polling WooCommerce order notes every 15 minutes. Detects tracking links, sends the WhatsApp notification, and writes `_wa_tracking_sent = true` to Woo order meta to prevent duplicates.

---

## 5. Database Schema

Only **three tables** exist locally. Everything else lives in WooCommerce.

### 5.1 `checkout_sessions` table
The bot's working memory for a live conversation. One active session per phone number at a time.

```
checkout_sessions
├── id                      UUID (PK)
├── phone                   VARCHAR  (E.164, e.g. 919820XXXXXX)
├── state                   VARCHAR  (enum — see State Machine section)
├── cart                    JSONB    ([{woo_product_id, name, qty, unit_price}])
│                                     snapshot from Woo order webhook at session start
├── woo_pending_order_id    INTEGER  (the preliminary Woo order that triggered this session)
├── selected_address_id     UUID     (FK → addresses.id, nullable)
├── shipping_cost           NUMERIC(10,2)  (30.00)
├── coupon_code             VARCHAR  (nullable)
├── discount_amount         NUMERIC(10,2)  (default 0)
├── discount_type           VARCHAR  (percentage | fixed_cart | fixed_product)
├── payment_method          VARCHAR  (cod | razorpay)
├── razorpay_link_id        VARCHAR  (nullable)
├── razorpay_payment_id     VARCHAR  (nullable)
├── subtotal                NUMERIC(10,2)
├── total                   NUMERIC(10,2)
├── woo_confirmed_order_id  INTEGER  (nullable — set after confirmed order created in Woo)
├── expires_at              TIMESTAMP  (24h from last update)
├── created_at              TIMESTAMP
└── updated_at              TIMESTAMP
```

> The cart is a snapshot taken at session start from the webhook payload. It is not kept in sync with WooCommerce during the session — stock is validated once at session creation via the Woo API, and the snapshot is used for the order summary and final order payload.

---

### 5.2 `addresses` table
Saved delivery addresses keyed directly by phone number. WhatsApp users have no login so addresses cannot live in WooCommerce customer accounts without a matching account.

```
addresses
├── id            UUID (PK)
├── phone         VARCHAR  (E.164) — no users table, keyed directly to phone
├── full_name     VARCHAR
├── line1         VARCHAR
├── line2         VARCHAR  (nullable)
├── city          VARCHAR
├── state         VARCHAR
├── pincode       VARCHAR
├── label         VARCHAR  (e.g. "Home", "Office", nullable)
├── is_last_used  BOOLEAN  (default false)
├── created_at    TIMESTAMP
└── updated_at    TIMESTAMP
```

---

### 5.3 `message_logs` table
Full log of every inbound and outbound WhatsApp message. Purely operational — used for debugging broken sessions, auditing flows, and deduplicating incoming webhooks via `wamid`.

```
message_logs
├── id            UUID (PK)
├── phone         VARCHAR
├── direction     VARCHAR  (inbound | outbound)
├── wamid         VARCHAR  (WhatsApp Message ID — for deduplication)
├── message_type  VARCHAR  (text | interactive | template | button)
├── body          TEXT
├── session_id    UUID     (nullable, FK → checkout_sessions.id)
├── created_at    TIMESTAMP
```

---

### What Lives in WooCommerce (not locally)

| Data | WooCommerce Endpoint |
|---|---|
| Products & stock | `GET /wc/v3/products` |
| Product categories | `GET /wc/v3/products/categories` |
| Coupon rules & validity | `GET /wc/v3/coupons` |
| All confirmed orders | `GET/POST /wc/v3/orders` |
| Customer details | `GET /wc/v3/customers` |
| Tracking links | `GET /wc/v3/orders/{id}/notes` |
| Tracking sent flag | Order meta: `_wa_tracking_sent` |
| WhatsApp order flag | Order meta: `_whatsapp_order` |

---

## 6. Catalog Sync Engine

### 6.1 Triggers

| Trigger | Action |
|---|---|
| Webhook: `product.created` | Fetch from Woo, push to Meta catalog immediately |
| Webhook: `product.updated` | Fetch from Woo, update in Meta catalog immediately |
| Webhook: `product.deleted` | Delete from Meta catalog immediately |
| APScheduler: every 6 hours | Full paginated pull from Woo → batch upsert to Meta |

No local product table. On a webhook event, the service calls `GET /wc/v3/products/{id}`, transforms the fields, and pushes to Meta in one shot.

### 6.2 Field Mapping (WooCommerce → Meta Commerce)

| WooCommerce Field | Meta Catalog Field | Notes |
|---|---|---|
| `id` | `retailer_id` | Prefixed: `woo_{id}` |
| `name` | `name` | |
| `short_description` (fallback `description`) | `description` | Strip HTML |
| `sale_price` (fallback `price`) | `price` | In paise (× 100) |
| `"INR"` | `currency` | |
| `stock_status` | `availability` | `instock` → `in stock` |
| `images[0].src` | `image_url` | Public HTTPS only |
| `permalink` | `url` | |
| `categories[0].name` (mapped) | `category` | Via `category_map.json` |
| `"new"` | `condition` | Always new |

### 6.3 Category Mapping

Meta uses a fixed taxonomy. Maintain `sync/category_map.json`:

```json
{
  "Apparel": "Apparel & Accessories > Clothing",
  "Footwear": "Apparel & Accessories > Shoes",
  "Electronics": "Electronics",
  "Home Decor": "Home & Garden",
  "default": "Generic"
}
```

### 6.4 Batch Upsert Strategy

For full sync, chunk products into batches of 1000:
```
POST https://graph.facebook.com/v19.0/{catalog_id}/items_batch
{ "allow_upsert": true, "requests": [...] }
```

`allow_upsert: true` creates new and updates existing in one call. For deletions during full sync, compare `retailer_id` values in Meta against Woo's current product list and issue `DELETE` method entries for orphans.

---

## 7. Conversation State Machine

Every user has exactly one active session at a time, keyed by phone. The `state` field controls what the bot expects next.

### 7.1 States

```
IDLE
  │
  └─► [WooCommerce order.created webhook received]
        │
        ▼
WELCOME_SENT
  │  Bot: "Your order is being prepared! Let's complete checkout."
  │  Buttons: [Continue Checkout] [Cancel]
  │
  ├─► [Cancel] ──────────────────────────────► SESSION_CANCELLED
  │
  └─► [Continue Checkout]
        │
        ▼
ADDRESS_CHECK
  │  Saved address exists?
  │  → Yes: show address, ask to use or enter new (list if 2+)
  │  → No: go directly to name collection
  │
  ├─► [Use saved] ────────────────────────────► ADDRESS_CONFIRMED
  │
  └─► [New address]
        │
COLLECTING_ADDRESS_NAME
COLLECTING_ADDRESS_LINE1
COLLECTING_ADDRESS_LINE2   (user can type "skip")
COLLECTING_ADDRESS_CITY
COLLECTING_ADDRESS_STATE
COLLECTING_ADDRESS_PINCODE
        │
        ▼
ADDRESS_CONFIRMED
        │
        ▼
COUPON_PROMPT
  │  Buttons: [Yes, I have a code] [Skip]
  │
  ├─► [Skip] ─────────────────────────────────► ORDER_SUMMARY
  │
  └─► [Yes]
        │
COLLECTING_COUPON
  │  Validate live against Woo Coupons API
  │
  ├─► [Invalid] → re-prompt (max 3 attempts, then auto-skip)
  └─► [Valid] → apply discount
        │
        ▼
ORDER_SUMMARY
  │  Shipping added (₹30)
  │  Full summary message sent
  │
  └─► PAYMENT_METHOD_SELECT
        │  Buttons: [Cash on Delivery] [Pay Online]
        │
        ├─► [COD] ──────────────────────────────► ORDER_CONFIRM_PROMPT
        │
        └─► [Pay Online]
              │
PAYMENT_LINK_SENT
  │  Razorpay link sent
  │  Poll every 2 min, expire after 30 min
  │
  ├─► [payment.captured webhook] ─────────────► ORDER_CONFIRM_PROMPT
  └─► [timeout/failed] → retry or switch to COD
        │
        ▼
ORDER_CONFIRM_PROMPT
  │  Full summary shown again
  │  Buttons: [✅ Confirm Order] [✏️ Edit Address] [❌ Cancel]
  │
  ├─► [Edit Address] → ADDRESS_CHECK (cart + coupon + payment preserved)
  ├─► [Cancel] ───────────────────────────────► SESSION_CANCELLED
  │
  └─► [Confirm Order]
        │
ORDER_PLACING
  │  Woo confirmed order created via REST API
  │  woo_pending_order cancelled
  │
  └─► ORDER_PLACED  (terminal)
        │
        Bot: "Order #XYZ confirmed 🎉"
        Session archived → Tracking monitor takes over
```

### 7.2 Session Timeout

Sessions inactive for **24 hours** in any non-terminal state are expired by a cleanup job. Bot sends: *"Your checkout session has expired. Browse our catalog again to start a new order."*

Terminal states: `ORDER_PLACED`, `SESSION_CANCELLED`.

### 7.3 Interruption Handling

Unexpected input at any step → re-send current prompt with a hint. After 5 consecutive unexpected inputs in the same state → offer: *"Type RESTART to start over."*

---

## 8. Full Checkout Flow

### 8.1 Entry Point

When a user places an order via WhatsApp Catalog, WooCommerce fires `order.created`. The service:

1. Extracts `billing.phone`, `line_items`, and `id` from the webhook
2. Normalises phone to E.164
3. Validates stock for each cart item via `GET /wc/v3/products/{id}`
4. Creates `checkout_session` (state = `WELCOME_SENT`, cart snapshot, `woo_pending_order_id` set)
5. Sends welcome message via Meta Cloud API

> Configure WooCommerce to suppress its default "order received" notification for WhatsApp-sourced orders. Use a `woocommerce_new_order` hook to detect `_whatsapp_order` meta and skip the default email/SMS.

### 8.2 Address Flow

**Returning user:**
```
Bot: "Welcome back, Raj! 👋
      Deliver to your saved address?

      📍 Home — Flat 4B, Sunshine Apartments, Linking Road
                 Mumbai, Maharashtra - 400050

      [✅ Yes, deliver here]   [📝 Use a different address]"
```

If user has 2+ saved addresses, use a WhatsApp **list message** to show all options plus "Enter new address."

**New address collection:**
```
Bot: "Full name for delivery?"
User: "Raj Sharma"

Bot: "House / Flat / Building?"
User: "Flat 4B, Sunshine Apartments"

Bot: "Street / Area? (type 'skip' to skip)"
User: "Linking Road"

Bot: "City?"   →  "Mumbai"
Bot: "State?"  →  "Maharashtra"
Bot: "Pincode?"  →  "400050"

Bot: "Confirming:
      Raj Sharma, Flat 4B, Sunshine Apartments, Linking Road
      Mumbai, Maharashtra - 400050

      [✅ Correct]   [🔄 Re-enter]"
```

On confirmation, save to `addresses` with `is_last_used = true`, flip all others for this phone to `false`.

### 8.3 Order Summary Message

```
🛍️ *Order Summary*

📦 Items:
  • Blue Cotton T-Shirt (M) × 2 — ₹1,198
  • Black Joggers (L) × 1 — ₹899

━━━━━━━━━━━━━━━━━━━
Subtotal:           ₹2,097
Discount (SAVE10):  -₹209
Shipping:           ₹30
━━━━━━━━━━━━━━━━━━━
*Total:             ₹1,918*

🏠 Delivering to:
Raj Sharma
Flat 4B, Sunshine Apartments, Linking Road
Mumbai, Maharashtra - 400050
```

### 8.4 Confirmation

Three interactive reply buttons:
- `✅ Confirm Order`
- `✏️ Edit Address` → returns to `ADDRESS_CHECK`, preserving cart, coupon, payment method
- `❌ Cancel Order` → cancel the pending Woo order (`PUT /wc/v3/orders/{id}` → `cancelled`), close session

---

## 9. Shipping Engine

Fixed ₹30 on every order.

```env
SHIPPING_FLAT_AMOUNT=30
```

```python
# engines/shipping.py
def calculate_shipping(cart, address) -> float:
    return float(settings.SHIPPING_FLAT_AMOUNT)  # 30.0
```

No conditions, no zones, no weight logic. ₹30 always.

**Changing later:** Every part of the bot calls `calculate_shipping()` as a single function. To switch to zone-based or weight-based logic, only this file changes.

Future modes ready to drop in:
- Flat rate with free-shipping threshold
- Pincode / zone based (Mumbai local, metro, rest of India)
- Weight slabs
- WooCommerce shipping zones pull

---

## 10. Discounts & Coupons

### 10.1 Validation Flow

1. User enters code
2. `GET /wc/v3/coupons?code={code}` on WooCommerce
3. Validate all native constraints:

| Constraint | Check |
|---|---|
| `date_expires` | Null or in the future |
| `usage_limit` | `usage_count` < limit |
| `usage_limit_per_user` | Query user's past Woo orders by phone |
| `minimum_amount` | Cart subtotal ≥ value |
| `maximum_amount` | Cart subtotal ≤ value (if set) |
| `product_ids` | At least one cart item in allowed list |
| `excluded_product_ids` | No cart item in exclusion list |

4. Invalid → tell user why, retry up to 3 times, then auto-skip
5. Valid → calculate discount, show in summary

### 10.2 Discount Calculation

| `discount_type` | Formula |
|---|---|
| `percent` | `subtotal × (amount / 100)` |
| `fixed_cart` | Flat `amount` off subtotal |
| `fixed_product` | `amount × qty` per matched product |

Discount applied **before** shipping — ₹30 is always added on top of post-discount subtotal.

### 10.3 Display in Summary

```
Discount (SAVE10 - 10%):  -₹209
```
or
```
Discount (FLAT50):        -₹50
```

Coupon code is passed to WooCommerce at order creation via `coupon_lines` so usage is tracked correctly.

---

## 11. Payment Handling

### 11.1 COD Flow

1. User selects "Cash on Delivery"
2. Proceed to `ORDER_CONFIRM_PROMPT`
3. On confirmation → Woo order created with `payment_method: "cod"`, `set_paid: false`, `status: "processing"`

### 11.2 Online Payment Flow (Razorpay)

**Generate link:**
```
POST https://api.razorpay.com/v1/payment_links
{
  "amount": 191800,
  "currency": "INR",
  "description": "WhatsApp Order",
  "customer": { "name": "Raj Sharma", "contact": "+919820XXXXXX" },
  "notify": { "sms": false, "email": false },
  "reminder_enable": false,
  "callback_url": "https://yourdomain.com/api/payments/razorpay/callback",
  "expire_by": {epoch: now + 30 min}
}
```

**Send to user:**
```
Bot: "💳 Complete your payment:
      https://rzp.io/l/XXXXXXXX

      Link expires in 30 minutes. Order confirmed automatically on payment.

      [🤝 Switch to Cash on Delivery]"
```

**Payment captured webhook (`POST /api/payments/razorpay/webhook`):**
1. Verify Razorpay HMAC signature
2. Match `payment_link_id` → `checkout_sessions.razorpay_link_id`
3. Save `razorpay_payment_id` to session
4. Bot: "Payment received ✅ Confirming your order..."
5. Auto-advance to order creation (no manual confirm needed)

**Timeout (30 min, polled every 2 min):**
```
Bot: "⏰ Payment link expired.
      [🔄 New Payment Link]   [🤝 Switch to COD]"
```

**Switch to COD mid-flow:** Cancel/expire Razorpay link, update session `payment_method = cod`, resume from `ORDER_CONFIRM_PROMPT`.

---

## 12. Order Sync to WooCommerce

### 12.1 Order Creation Payload

```
POST /wc/v3/orders
{
  "payment_method": "cod" | "razorpay",
  "payment_method_title": "Cash on Delivery" | "Razorpay",
  "set_paid": false | true,
  "status": "processing",
  "billing": {
    "first_name": "Raj", "last_name": "Sharma",
    "address_1": "Flat 4B, Sunshine Apartments",
    "address_2": "Linking Road",
    "city": "Mumbai", "state": "MH",
    "postcode": "400050", "country": "IN",
    "phone": "+919820XXXXXX"
  },
  "shipping": { ...same as billing... },
  "line_items": [
    { "product_id": 123, "quantity": 2 },
    { "product_id": 456, "quantity": 1 }
  ],
  "shipping_lines": [{
    "method_id": "whatsapp_flat",
    "method_title": "WhatsApp Checkout",
    "total": "30.00"
  }],
  "coupon_lines": [{ "code": "SAVE10" }],
  "meta_data": [
    { "key": "_whatsapp_order",      "value": "true" },
    { "key": "_whatsapp_session_id", "value": "{session_id}" },
    { "key": "_wa_tracking_sent",    "value": "false" }
  ]
}
```

### 12.2 Post-Creation Steps

1. Save `woo_confirmed_order_id` to session
2. Send confirmation message to user with order ID
3. Cancel `woo_pending_order_id` via `PUT /wc/v3/orders/{id}` → `{ "status": "cancelled" }`
4. Archive session (state = `ORDER_PLACED`)

### 12.3 Why Two Woo Orders?

When a user places an order via WhatsApp Catalog, WooCommerce auto-creates a `pending` order. The bot then creates a proper `processing` order after checkout is complete. The original `pending` order is cancelled. This keeps Woo clean — only one real order per transaction.

---

## 13. Tracking Link Monitor

### 13.1 How It Works

APScheduler runs every **15 minutes**:

```
GET /wc/v3/orders?meta_key=_wa_tracking_sent&meta_value=false&status=processing,completed
```

For each result, fetch order notes:
```
GET /wc/v3/orders/{id}/notes
```

### 13.2 Link Detection

Scan each note for:
- URLs containing: `track`, `tracking`, `shiprocket`, `delhivery`, `dtdc`, `bluedart`, `ecom`, `ekart`, `xpressbees`
- Or any raw URL: `https?://[^\s]+`

First match = tracking link.

### 13.3 WhatsApp Message

```
Bot: "📦 Your order #{id} has been shipped!

      Track your package:
      🔗 https://tracking.delhivery.com/XXXXX

      Estimated delivery: 3–5 business days.
      Any questions? Just reply here 😊"
```

### 13.4 Duplicate Prevention

After sending:
```
PUT /wc/v3/orders/{id}
{ "meta_data": [{ "key": "_wa_tracking_sent", "value": "true" }] }
```

Next poll skips this order. No local table needed.

### 13.5 Optional Real-Time Trigger

Configure a Woo webhook for `order.updated`. On receipt, check if `_wa_tracking_sent` is still `false` and scan new notes — send immediately without waiting 15 minutes.

---

## 14. API Endpoints

### 14.1 Webhooks

| Method | Endpoint | Description |
|---|---|---|
| POST | `/webhooks/woo/order` | WooCommerce new order |
| POST | `/webhooks/woo/product` | Product create / update / delete |
| GET | `/webhooks/whatsapp` | Meta verification (hub.challenge) |
| POST | `/webhooks/whatsapp` | Inbound messages + delivery status |
| POST | `/api/payments/razorpay/webhook` | payment.captured event |
| GET | `/api/payments/razorpay/callback` | Post-payment redirect (UX fallback) |

### 14.2 Admin

| Method | Endpoint | Description |
|---|---|---|
| POST | `/admin/sync/full` | Trigger manual full catalog sync |
| GET | `/admin/sync/status` | Last sync run, count, failures |
| GET | `/admin/sessions` | All active checkout sessions |
| GET | `/admin/sessions/{phone}` | Session state for a phone number |
| DELETE | `/admin/sessions/{phone}` | Force-close a stuck session |
| GET | `/admin/users/{phone}/addresses` | Saved addresses for a phone |

### 14.3 Health

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Service liveness |
| GET | `/health/db` | DB connectivity |
| GET | `/health/woo` | WooCommerce API reachability |

---

## 15. Webhook Flows

### 15.1 Inbound WhatsApp Message

```
POST /webhooks/whatsapp
    │
    ├─► Verify X-Hub-Signature-256
    ├─► Deduplicate by wamid (check message_logs)
    ├─► Parse message type (text | interactive | order)
    ├─► Lookup active session by phone
    ├─► Route to state handler
    ├─► Handler processes input → updates DB → sends reply
    └─► Log to message_logs
```

### 15.2 WooCommerce Order Webhook

```
POST /webhooks/woo/order
    │
    ├─► Verify X-WC-Webhook-Signature
    ├─► Normalise billing.phone to E.164
    ├─► Check for existing active session on this phone
    │     └─► If exists: skip (duplicate webhook)
    ├─► Validate cart stock via Woo products API
    ├─► Create checkout_session (state = WELCOME_SENT)
    └─► Send WhatsApp welcome message
```

### 15.3 WooCommerce Product Webhook

```
POST /webhooks/woo/product
    │
    ├─► Verify webhook secret
    ├─► Determine: created | updated | deleted
    │
    ├─► created / updated:
    │     ├─► GET /wc/v3/products/{id}
    │     ├─► Transform fields
    │     └─► Upsert to Meta Commerce API
    │
    └─► deleted:
          └─► DELETE from Meta Commerce API by retailer_id
```

---

## 16. Message Templates

### 16.1 Templates Needing Meta Approval

All first-contact messages (or re-contacts after 24h) must use approved templates.

| Template Name | Trigger | Category |
|---|---|---|
| `checkout_welcome` | New order webhook | Utility |
| `order_confirmed` | Order placed | Utility |
| `payment_link` | Razorpay link generated | Utility |
| `tracking_update` | Tracking link found | Utility |
| `session_expired` | 24h inactivity | Utility |

> Submit all five templates on **Day 1** of the project. Approval takes 24–72h. Do not leave this for the deployment phase.

### 16.2 Interactive Message Types

| Type | Used For |
|---|---|
| `button` reply (up to 3) | Yes/No, payment method, confirm/cancel |
| `list` message (up to 10 items) | Choosing from multiple saved addresses |
| Plain `text` | Address field input, coupon code input |

---

## 17. Config & Environment Variables

```env
# WooCommerce
WOO_BASE_URL=https://yourstore.com
WOO_CONSUMER_KEY=ck_XXXX
WOO_CONSUMER_SECRET=cs_XXXX
WOO_WEBHOOK_SECRET=your_woo_webhook_secret

# Meta / WhatsApp Cloud API
META_PHONE_NUMBER_ID=1234567890
META_ACCESS_TOKEN=EAAxxxx
META_VERIFY_TOKEN=your_verify_token
META_CATALOG_ID=your_catalog_id
META_API_VERSION=v19.0

# Razorpay
RAZORPAY_KEY_ID=rzp_live_xxx
RAZORPAY_KEY_SECRET=xxx
RAZORPAY_WEBHOOK_SECRET=xxx

# Database (3 tables only)
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/whatsapp_bot

# App
APP_BASE_URL=https://yourdomain.com
SESSION_EXPIRY_HOURS=24
PAYMENT_LINK_EXPIRY_MINUTES=30
MAX_COUPON_ATTEMPTS=3

# Shipping
SHIPPING_FLAT_AMOUNT=30

# Region
CURRENCY=INR
COUNTRY=IN
```

---

## 18. Error Handling & Edge Cases

### 18.1 WooCommerce API Unreachable
Retry 3 times with exponential backoff (5s → 15s → 45s). If all fail: notify admin, respond to user: *"We're experiencing a small hiccup. Please try again in a few minutes."*

### 18.2 Meta API Rate Limits
Cloud API: 250 msg/sec (unlikely to hit). Commerce Batch API: retry on `(#32)` with 60s backoff.

### 18.3 Unexpected User Input
Re-prompt current step. After 5 consecutive unexpected inputs: *"Type RESTART to start over."*

### 18.4 Duplicate Order Webhooks
WooCommerce retries failed webhooks — deduplicate by checking `woo_pending_order_id` in `checkout_sessions` before creating a new session.

### 18.5 Two Active Checkouts for Same Phone
Check for active session before creating a new one. If found: *"You have a checkout in progress. Let's continue from where you left off."*

### 18.6 Product Out of Stock at Session Start
Validate each cart item via Woo API on session creation. Remove OOS items, notify user. If all OOS: cancel gracefully.

### 18.7 Coupon Rejected at Order Creation
WooCommerce re-validates coupons at order creation. If rejected (expired or hit limit between validation and confirm): strip coupon from session, recalculate total, re-show confirm prompt.

### 18.8 Razorpay Payment After Session Expiry
Payment webhook arrives but session is expired. Look up by `razorpay_link_id`, issue a refund via Razorpay API, notify user and admin.

### 18.9 Malformed Phone Numbers
WooCommerce billing.phone may arrive as `09820XXXXXX` or `9820XXXXXX`. Always normalise to E.164 (`919820XXXXXX`) in a centralised `utils/phone.py` before any DB or API operation.

---

## 19. Folder Structure

```
whatsapp-woo-bot/
│
├── main.py                          # FastAPI app init, router registration
├── config.py                        # pydantic BaseSettings + .env
├── scheduler.py                     # APScheduler job registration
│
├── routers/
│   ├── whatsapp_webhook.py          # GET + POST /webhooks/whatsapp
│   ├── woo_webhook.py               # POST /webhooks/woo/order + /product
│   ├── payment_webhook.py           # Razorpay webhook + callback
│   └── admin.py                     # Admin endpoints
│
├── bot/
│   ├── state_machine.py             # Routes state → handler
│   ├── session_manager.py           # CRUD for checkout_sessions
│   └── states/
│       ├── welcome.py
│       ├── address.py
│       ├── coupon.py
│       ├── summary.py
│       ├── payment.py
│       └── confirm.py
│
├── clients/
│   ├── whatsapp.py                  # Meta Cloud API (messages, templates)
│   ├── woocommerce.py               # Woo REST API (products, orders, coupons, notes, meta)
│   ├── meta_commerce.py             # Meta Commerce API (catalog upsert/delete)
│   └── razorpay.py                  # Razorpay payment links
│
├── engines/
│   ├── shipping.py                  # calculate_shipping() — ₹30 flat, swappable
│   └── coupon.py                    # Live Woo coupon validation + discount calc
│
├── sync/
│   ├── catalog_sync.py              # Full sync orchestrator
│   ├── product_transformer.py       # Woo → Meta field mapping
│   └── category_map.json            # Woo category → Meta taxonomy
│
├── monitors/
│   └── tracking_monitor.py          # Polls Woo notes, sends WA tracking message
│
├── db/
│   ├── database.py                  # Async engine + session factory
│   ├── models.py                    # 3 tables: checkout_sessions, addresses, message_logs
│   └── migrations/                  # Alembic
│
├── utils/
│   ├── phone.py                     # E.164 normalisation
│   ├── html_strip.py                # Strip HTML from Woo descriptions
│   ├── signature.py                 # HMAC verification (Woo + Meta + Razorpay)
│   └── retry.py                     # Exponential backoff decorator
│
├── templates/
│   └── message_templates.py         # WhatsApp template message builders
│
├── .env
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## 20. Build Phases & Effort Estimate

### Phase 1 — Foundation (2–3 days)
- Project scaffold, PostgreSQL setup, Alembic migrations (3 tables)
- WooCommerce client (products, orders, coupons, notes, order meta)
- Meta Cloud API client (text, buttons, list, templates)
- WhatsApp webhook receiver + signature verification + wamid deduplication
- WooCommerce webhook receiver + signature verification
- Phone normaliser (`utils/phone.py`)

### Phase 2 — Catalog Sync (2 days)
- Meta Commerce API client (batch upsert, single upsert/delete)
- Product field transformer + HTML stripper
- Category map config
- Full sync APScheduler job (paginated)
- Real-time sync on product webhooks

### Phase 3 — Checkout Bot Core (4–5 days)
- State machine router
- Welcome, address collection, saved address selection (list message for 2+ addresses)
- Session manager (create, read, update, expire)
- Coupon engine (live Woo validation + all discount types)
- Order summary message builder
- Shipping (₹30 flat via `calculate_shipping()`)

### Phase 4 — Payments (2 days)
- Razorpay client (create, cancel, status poll)
- COD flow
- Payment link message + 2-min poll + 30-min timeout job
- Razorpay webhook handler
- Switch to COD mid-flow

### Phase 5 — Order Sync & Confirmation (2 days)
- WooCommerce order creation payload builder
- Post-confirmation order creation + pending order cancellation
- Session archival
- Confirmation message to user

### Phase 6 — Tracking Monitor (1 day)
- APScheduler poll job
- Tracking link regex
- WhatsApp tracking notification
- Woo order meta update (`_wa_tracking_sent`)

### Phase 7 — Hardening & Admin (2 days)
- Admin endpoints
- Error handling, retry logic, edge case coverage
- Logging throughout all components

### Phase 8 — Deployment + Template Approval (2–3 days)
- VPS: Nginx + Uvicorn + PM2
- SSL + domain config
- 5 Meta template submissions (do on Day 1, not here)
- End-to-end test run

---

**Total Estimated Build Time: ~15–19 days (solo)**

~5 days leaner than the over-engineered first version — no local product mirror, no local orders table, no users table. WooCommerce does what it was built to do.

---

*Document Version: 2.0 — Last Updated: April 2026*
