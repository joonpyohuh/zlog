import {createClient} from '@/lib/supabase/server';

export type SubscriptionRow = {
  id: string;
  user_id: string;
  paddle_customer_id: string | null;
  paddle_subscription_id: string | null;
  paddle_transaction_id: string | null;
  paddle_price_id: string | null;
  plan: string;
  status: string;
  currency_code: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
  scheduled_change: unknown;
  canceled_at: string | null;
  last_event_at: string | null;
};

/** Official Paddle Billing statuses that grant Pro access. */
const PRO_STATUSES = new Set(['active', 'trialing']);

export function statusGrantsPro(status: string | null | undefined): boolean {
  if (!status) return false;
  return PRO_STATUSES.has(status.toLowerCase());
}

export async function getUserSubscription(userId: string): Promise<SubscriptionRow | null> {
  const supabase = await createClient();
  const {data, error} = await supabase
    .from('subscriptions')
    .select('*')
    .eq('user_id', userId)
    .maybeSingle();
  if (error) throw error;
  return data as SubscriptionRow | null;
}

export async function userHasProAccess(userId: string): Promise<boolean> {
  const sub = await getUserSubscription(userId);
  return Boolean(sub && sub.plan === 'pro' && statusGrantsPro(sub.status));
}

export async function requireProAccess(): Promise<{userId: string; subscription: SubscriptionRow}> {
  const supabase = await createClient();
  const {
    data: {user},
  } = await supabase.auth.getUser();
  if (!user) {
    throw new Error('UNAUTHORIZED');
  }
  const subscription = await getUserSubscription(user.id);
  if (!subscription || subscription.plan !== 'pro' || !statusGrantsPro(subscription.status)) {
    throw new Error('FORBIDDEN_PRO');
  }
  return {userId: user.id, subscription};
}

export function planLabel(plan: string, status: string): string {
  if (plan === 'pro' && statusGrantsPro(status)) return 'zlog Pro';
  return 'Free';
}
