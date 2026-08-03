import Link from 'next/link';
import {redirect} from 'next/navigation';
import {createClient} from '@/lib/supabase/server';
import {getUserSubscription, userHasProAccess} from '@/lib/billing';
import {UpgradeToProButton} from '@/components/billing/UpgradeToProButton';
import {ManageBillingButton} from '@/components/billing/ManageBillingButton';

function formatDate(value: string | null | undefined) {
  if (!value) return '—';
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export default async function BillingSettingsPage({
  searchParams,
}: {
  searchParams: Promise<{checkout?: string}>;
}) {
  const supabase = await createClient();
  const {
    data: {user},
  } = await supabase.auth.getUser();
  if (!user) redirect('/login?next=/settings/billing');

  const params = await searchParams;
  const sub = await getUserSubscription(user.id);
  const isPro = await userHasProAccess(user.id);
  const plan = sub?.plan ?? 'free';
  const status = sub?.status ?? 'inactive';

  return (
    <main className="mx-auto w-full max-w-2xl px-5 pb-24 pt-8">
      <h1 className="text-3xl font-medium text-white">Billing</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Subscription state is synced from verified Paddle webhooks. Paddle securely processes
        payments.
      </p>

      {params.checkout === 'success' && (
        <p className="mt-4 rounded-2xl border border-emerald-900/50 bg-emerald-950/40 px-4 py-3 text-sm text-emerald-100">
          Checkout submitted. Pro access activates after Paddle confirms the payment via webhook
          (usually a few seconds). Refresh if status is still Free.
        </p>
      )}

      <section className="mt-8 rounded-3xl border border-neutral-800 bg-black/70 p-6">
        <dl className="grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-neutral-500">Plan</dt>
            <dd className="mt-1 text-white capitalize">{plan}</dd>
          </div>
          <div>
            <dt className="text-neutral-500">Status</dt>
            <dd className="mt-1 text-white capitalize">{status.replaceAll('_', ' ')}</dd>
          </div>
          <div>
            <dt className="text-neutral-500">Pro access</dt>
            <dd className="mt-1 text-white">{isPro ? 'Enabled' : 'Disabled'}</dd>
          </div>
          <div>
            <dt className="text-neutral-500">Current period ends</dt>
            <dd className="mt-1 text-white">{formatDate(sub?.current_period_end)}</dd>
          </div>
        </dl>

        {(status === 'canceled' || status === 'past_due' || status === 'paused') && (
          <p className="mt-5 rounded-2xl border border-amber-900/60 bg-amber-950/40 px-4 py-3 text-sm text-amber-100">
            {status === 'canceled' &&
              'Your subscription is canceled. Pro access is disabled unless status is active/trialing.'}
            {status === 'past_due' &&
              'Payment is past due. Update your payment method in the Paddle portal to restore Pro.'}
            {status === 'paused' &&
              'Your subscription is paused. Resume billing in the Paddle portal to restore Pro.'}
          </p>
        )}

        <div className="mt-6 flex flex-wrap gap-3">
          {!isPro && <UpgradeToProButton />}
          {sub?.paddle_customer_id && <ManageBillingButton />}
          <Link
            href="/pricing"
            className="rounded-full border border-neutral-700 px-5 py-2.5 text-sm text-neutral-300"
          >
            View pricing
          </Link>
        </div>
      </section>

      <form action="/auth/signout" method="post" className="mt-6">
        <button
          type="submit"
          className="text-sm text-neutral-500 underline-offset-4 hover:underline"
        >
          Sign out
        </button>
      </form>
    </main>
  );
}
