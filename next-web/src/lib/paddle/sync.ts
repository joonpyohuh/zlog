import {createAdminClient} from '@/lib/supabase/admin';
import {statusGrantsPro} from '@/lib/billing';

type SyncInput = {
  userId?: string | null;
  paddleCustomerId?: string | null;
  paddleSubscriptionId?: string | null;
  paddleTransactionId?: string | null;
  paddlePriceId?: string | null;
  status?: string | null;
  currencyCode?: string | null;
  currentPeriodStart?: string | null;
  currentPeriodEnd?: string | null;
  scheduledChange?: unknown;
  canceledAt?: string | null;
  occurredAt?: string | null;
  plan?: 'free' | 'pro';
};

function asIso(value: string | number | null | undefined): string | null {
  if (value == null || value === '') return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

async function isOlderThanStored(
  admin: ReturnType<typeof createAdminClient>,
  userId: string,
  occurredAt?: string | null,
): Promise<boolean> {
  if (!occurredAt) return false;
  const {data} = await admin
    .from('subscriptions')
    .select('last_event_at')
    .eq('user_id', userId)
    .maybeSingle();
  if (!data?.last_event_at) return false;
  const existing = new Date(data.last_event_at).getTime();
  const incoming = new Date(occurredAt).getTime();
  return !Number.isNaN(existing) && !Number.isNaN(incoming) && incoming < existing;
}

export async function upsertSubscriptionFromPaddle(input: SyncInput): Promise<void> {
  const admin = createAdminClient();
  let userId = input.userId ?? null;

  if (!userId && input.paddleSubscriptionId) {
    const {data} = await admin
      .from('subscriptions')
      .select('user_id')
      .eq('paddle_subscription_id', input.paddleSubscriptionId)
      .maybeSingle();
    userId = data?.user_id ?? null;
  }

  if (!userId && input.paddleCustomerId) {
    const {data} = await admin
      .from('subscriptions')
      .select('user_id')
      .eq('paddle_customer_id', input.paddleCustomerId)
      .maybeSingle();
    userId = data?.user_id ?? null;
  }

  if (!userId) {
    throw new Error('Unable to resolve Supabase user for Paddle event');
  }

  if (await isOlderThanStored(admin, userId, input.occurredAt)) {
    return;
  }

  const status = (input.status ?? 'inactive').toLowerCase();
  const configuredPrice = process.env.NEXT_PUBLIC_PADDLE_PRO_PRICE_ID ?? '';
  const isProPrice =
    Boolean(input.paddlePriceId) &&
    Boolean(configuredPrice) &&
    input.paddlePriceId === configuredPrice;
  const plan: 'free' | 'pro' =
    input.plan === 'pro' || isProPrice || (statusGrantsPro(status) && !configuredPrice)
      ? 'pro'
      : 'free';

  const row = {
    user_id: userId,
    paddle_customer_id: input.paddleCustomerId ?? null,
    paddle_subscription_id: input.paddleSubscriptionId ?? null,
    paddle_transaction_id: input.paddleTransactionId ?? null,
    paddle_price_id: input.paddlePriceId ?? null,
    plan,
    status,
    currency_code: input.currencyCode ?? null,
    current_period_start: asIso(input.currentPeriodStart),
    current_period_end: asIso(input.currentPeriodEnd),
    scheduled_change: input.scheduledChange ?? null,
    canceled_at: asIso(input.canceledAt),
    last_event_at: asIso(input.occurredAt) ?? new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };

  // Avoid wiping IDs with null when a later event omits them.
  const {data: existing} = await admin
    .from('subscriptions')
    .select(
      'paddle_customer_id, paddle_subscription_id, paddle_transaction_id, paddle_price_id, plan',
    )
    .eq('user_id', userId)
    .maybeSingle();

  const merged = {
    ...row,
    paddle_customer_id: row.paddle_customer_id ?? existing?.paddle_customer_id ?? null,
    paddle_subscription_id:
      row.paddle_subscription_id ?? existing?.paddle_subscription_id ?? null,
    paddle_transaction_id:
      row.paddle_transaction_id ?? existing?.paddle_transaction_id ?? null,
    paddle_price_id: row.paddle_price_id ?? existing?.paddle_price_id ?? null,
    plan: row.plan === 'free' && existing?.plan === 'pro' && !input.plan && !isProPrice
      ? 'pro'
      : row.plan,
  };

  const {error} = await admin.from('subscriptions').upsert(merged, {onConflict: 'user_id'});
  if (error) throw error;

  const isPro = merged.plan === 'pro' && statusGrantsPro(status);
  const {error: profileError} = await admin
    .from('profiles')
    .update({
      plan: merged.plan,
      is_pro: isPro,
      updated_at: new Date().toISOString(),
    })
    .eq('id', userId);
  if (profileError) throw profileError;
}
