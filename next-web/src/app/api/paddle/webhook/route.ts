import {NextResponse} from 'next/server';
import {createAdminClient} from '@/lib/supabase/admin';
import {getPaddleServer} from '@/lib/paddle/server';
import {upsertSubscriptionFromPaddle} from '@/lib/paddle/sync';

export const runtime = 'nodejs';

type CustomData = {
  user_id?: string;
  plan?: string;
  source?: string;
};

function pickCustomData(data: unknown): CustomData {
  if (!data || typeof data !== 'object') return {};
  const custom = (data as {customData?: unknown; custom_data?: unknown}).customData
    ?? (data as {custom_data?: unknown}).custom_data;
  if (!custom || typeof custom !== 'object') return {};
  const c = custom as Record<string, unknown>;
  return {
    user_id: typeof c.user_id === 'string' ? c.user_id : undefined,
    plan: typeof c.plan === 'string' ? c.plan : undefined,
    source: typeof c.source === 'string' ? c.source : undefined,
  };
}

export async function POST(request: Request) {
  const signature = request.headers.get('paddle-signature') ?? '';
  const rawBody = await request.text();
  const secret = process.env.PADDLE_WEBHOOK_SECRET;

  if (!secret) {
    console.error('paddle webhook: missing PADDLE_WEBHOOK_SECRET');
    return NextResponse.json({error: 'misconfigured'}, {status: 500});
  }
  if (!signature || !rawBody) {
    return NextResponse.json({error: 'missing signature or body'}, {status: 400});
  }

  let event: {
    eventId: string;
    eventType: string;
    occurredAt?: string;
    data: Record<string, unknown>;
  };

  try {
    const paddle = getPaddleServer();
    const verified = await paddle.webhooks.unmarshal(rawBody, secret, signature);
    event = {
      eventId: verified.eventId,
      eventType: String(verified.eventType),
      occurredAt: verified.occurredAt ? String(verified.occurredAt) : undefined,
      data: verified.data as unknown as Record<string, unknown>,
    };
  } catch {
    console.error('paddle webhook: invalid signature');
    return NextResponse.json({error: 'invalid signature'}, {status: 401});
  }

  const admin = createAdminClient();
  const {data: existing} = await admin
    .from('paddle_webhook_events')
    .select('event_id')
    .eq('event_id', event.eventId)
    .maybeSingle();

  if (existing) {
    return NextResponse.json({ok: true, duplicate: true});
  }

  const {error: insertError} = await admin.from('paddle_webhook_events').insert({
    event_id: event.eventId,
    event_type: event.eventType,
    occurred_at: event.occurredAt ?? null,
    payload: {type: event.eventType, id: event.eventId},
  });
  if (insertError) {
    // race on unique event_id → treat as duplicate
    if (insertError.code === '23505') {
      return NextResponse.json({ok: true, duplicate: true});
    }
    console.error('paddle webhook: failed to record event');
    return NextResponse.json({error: 'db error'}, {status: 500});
  }

  try {
    await handleEvent(event);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'processing failed';
    await admin
      .from('paddle_webhook_events')
      .update({processing_error: message.slice(0, 500)})
      .eq('event_id', event.eventId);
    console.error('paddle webhook: processing error', message);
    return NextResponse.json({error: 'processing failed'}, {status: 500});
  }

  return NextResponse.json({ok: true});
}

async function handleEvent(event: {
  eventType: string;
  occurredAt?: string;
  data: Record<string, unknown>;
}) {
  const type = event.eventType;
  const data = event.data;
  const custom = pickCustomData(data);

  if (type === 'transaction.completed') {
    const items = (data.items as Array<{price?: {id?: string}}>) ?? [];
    const priceId = items[0]?.price?.id ?? null;
    const customerId =
      (data.customerId as string | undefined) ??
      (data.customer_id as string | undefined) ??
      null;
    const subscriptionId =
      (data.subscriptionId as string | undefined) ??
      (data.subscription_id as string | undefined) ??
      null;
    await upsertSubscriptionFromPaddle({
      userId: custom.user_id,
      paddleCustomerId: customerId,
      paddleSubscriptionId: subscriptionId,
      paddleTransactionId: (data.id as string | undefined) ?? null,
      paddlePriceId: priceId,
      status: 'active',
      currencyCode: (data.currencyCode as string | undefined) ?? null,
      plan: custom.plan === 'pro' ? 'pro' : undefined,
      occurredAt: event.occurredAt,
    });
    return;
  }

  const subscriptionTypes = new Set([
    'subscription.created',
    'subscription.activated',
    'subscription.updated',
    'subscription.paused',
    'subscription.resumed',
    'subscription.canceled',
    'subscription.past_due',
  ]);

  if (!subscriptionTypes.has(type)) {
    return;
  }

  const status = String(data.status ?? 'inactive');
  const items = (data.items as Array<{price?: {id?: string}}>) ?? [];
  const priceId = items[0]?.price?.id ?? (data.priceId as string | undefined) ?? null;
  const customerId =
    (data.customerId as string | undefined) ??
    (data.customer_id as string | undefined) ??
    null;
  const currentBilling =
    (data.currentBillingPeriod as {startsAt?: string; endsAt?: string} | undefined) ??
    (data.current_billing_period as {starts_at?: string; ends_at?: string} | undefined);

  await upsertSubscriptionFromPaddle({
    userId: custom.user_id,
    paddleCustomerId: customerId,
    paddleSubscriptionId: (data.id as string | undefined) ?? null,
    paddlePriceId: priceId,
    status,
    currencyCode: (data.currencyCode as string | undefined) ?? null,
    currentPeriodStart:
      currentBilling && 'startsAt' in currentBilling
        ? currentBilling.startsAt
        : (currentBilling as {starts_at?: string} | undefined)?.starts_at,
    currentPeriodEnd:
      currentBilling && 'endsAt' in currentBilling
        ? currentBilling.endsAt
        : (currentBilling as {ends_at?: string} | undefined)?.ends_at,
    scheduledChange: data.scheduledChange ?? data.scheduled_change ?? null,
    canceledAt:
      (data.canceledAt as string | undefined) ??
      (data.canceled_at as string | undefined) ??
      null,
    plan: custom.plan === 'pro' ? 'pro' : undefined,
    occurredAt: event.occurredAt,
  });
}
