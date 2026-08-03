# Paddle Billing setup (zlog)

Billing lives in the Next.js app under `next-web/`. Entitlements are updated **only** by verified Paddle webhooks — never by client checkout success.

## Sandbox setup

1. Create a Paddle account and open **Sandbox**.
2. Complete seller/business profile as required by Paddle.
3. Stay in sandbox until the production checklist below.

## Product and price

1. Catalog → create product **zlog Pro**.
2. Add a **recurring monthly** price.
3. Copy the price ID (`pri_...`) into `NEXT_PUBLIC_PADDLE_PRO_PRICE_ID`.

## Client-side token

1. Developer tools → Authentication → **Client-side tokens**.
2. Create a sandbox token (`test_...`).
3. Set `NEXT_PUBLIC_PADDLE_CLIENT_TOKEN` and `NEXT_PUBLIC_PADDLE_ENV=sandbox`.

## API key

1. Developer tools → Authentication → **API keys**.
2. Create a sandbox secret key (`pdl_...`).
3. Set server-only `PADDLE_API_KEY` (never `NEXT_PUBLIC_`).

## Default payment link

1. Checkout → default payment link / retention settings (Paddle Billing).
2. Point the default payment link page to:

   `{NEXT_PUBLIC_APP_URL}/checkout`

3. That route initializes Paddle.js and opens overlay when `_ptxn` is present.

## Webhook destination

1. Developer tools → Notifications → create destination.
2. URL: `https://<your-host>/api/paddle/webhook` (local: use a tunnel such as ngrok).
3. Copy the endpoint secret into `PADDLE_WEBHOOK_SECRET`.
4. Enable at least:

   - `transaction.completed`
   - `subscription.created`
   - `subscription.activated`
   - `subscription.updated`
   - `subscription.paused`
   - `subscription.resumed`
   - `subscription.canceled`
   - `subscription.past_due`

## Environment variables

Copy `next-web/.env.example` → `next-web/.env.local` and fill:

| Variable | Where |
|---|---|
| `NEXT_PUBLIC_APP_URL` | Public app origin |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | Server only (webhooks) |
| `NEXT_PUBLIC_PADDLE_ENV` | `sandbox` or `production` |
| `NEXT_PUBLIC_PADDLE_CLIENT_TOKEN` | Paddle.js token |
| `NEXT_PUBLIC_PADDLE_PRO_PRICE_ID` | Monthly Pro price |
| `PADDLE_API_KEY` | Server only |
| `PADDLE_WEBHOOK_SECRET` | Server only |

Also configure Supabase Auth Site URL + redirect URLs to include `{APP_URL}/auth/callback`.

SQL schema: `supabase/migrations/20260804_paddle_billing.sql`.

## Local test flow

1. `cd next-web && npm install && npm run dev`
2. Apply the migration if the Supabase project does not already have tables.
3. Tunnel webhooks to `http://localhost:3000/api/paddle/webhook`.
4. Sign up / sign in at `/login`.
5. Open `/pricing` → **Upgrade to Pro** (overlay checkout).
6. Complete sandbox payment.
7. Confirm `/settings/billing` shows Pro after the webhook (refresh if needed).
8. Click **Manage billing** → Paddle customer portal.
9. Cancel / pause in portal → status updates via webhook; Pro access only for `active` / `trialing`.

## Status → entitlement mapping

| Paddle status | Pro access |
|---|---|
| `active`, `trialing` | Yes (if plan is Pro) |
| `canceled`, `paused`, `past_due`, `inactive` | No |

## Production launch checklist

- [ ] Switch Paddle to live (or create live catalog + keys)
- [ ] Set `NEXT_PUBLIC_PADDLE_ENV=production`
- [ ] Replace client token, API key, price ID, webhook secret with **live** values
- [ ] Point webhook + default payment link at the production host
- [ ] Rotate any keys that were ever pasted into chat or committed
- [ ] Confirm RLS: clients can only `SELECT` their own `subscriptions` / `profiles`
- [ ] Confirm Pro gates call `requireProAccess()` on server routes
- [ ] Smoke-test overlay checkout, webhook sync, and customer portal
