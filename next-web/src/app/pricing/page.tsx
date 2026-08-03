import Link from 'next/link';
import {createClient} from '@/lib/supabase/server';
import {userHasProAccess} from '@/lib/billing';
import {UpgradeToProButton} from '@/components/billing/UpgradeToProButton';
import {ManageBillingButton} from '@/components/billing/ManageBillingButton';

export default async function PricingPage() {
  const supabase = await createClient();
  const {
    data: {user},
  } = await supabase.auth.getUser();
  const isPro = user ? await userHasProAccess(user.id) : false;

  return (
    <main className="mx-auto w-full max-w-4xl px-5 pb-24 pt-8">
      <h1 className="text-3xl font-medium text-white">Pricing</h1>
      <p className="mt-2 max-w-xl text-sm text-neutral-500">
        Free to explore. Pro unlocks film creation. Payments are processed securely by Paddle.
      </p>

      <div className="mt-10 grid gap-5 md:grid-cols-2">
        <section className="rounded-3xl border border-neutral-800 bg-black/60 p-6">
          <h2 className="text-lg text-white">Free</h2>
          <p className="mt-1 text-3xl font-medium text-white">$0</p>
          <ul className="mt-5 space-y-2 text-sm text-neutral-400">
            <li>Browse the zlog studio UI</li>
            <li>Review taste examples locally</li>
            <li>No Pro film rendering entitlement</li>
          </ul>
          {!user && (
            <Link
              href="/login?next=/pricing"
              className="mt-6 inline-flex rounded-full border border-neutral-700 px-5 py-2.5 text-sm text-white"
            >
              Sign in
            </Link>
          )}
        </section>

        <section className="rounded-3xl border border-white/20 bg-neutral-950 p-6 shadow-xl">
          <h2 className="text-lg text-white">zlog Pro</h2>
          <p className="mt-1 text-3xl font-medium text-white">
            Monthly <span className="text-base text-neutral-500">via Paddle</span>
          </p>
          <ul className="mt-5 space-y-2 text-sm text-neutral-400">
            <li>Pro access for film generation</li>
            <li>Billing managed in Paddle portal</li>
            <li>Cancel anytime before renewal</li>
          </ul>
          <div className="mt-6">
            {!user ? (
              <Link
                href="/login?next=/pricing"
                className="inline-flex rounded-full bg-white px-5 py-2.5 text-sm font-medium text-black"
              >
                Sign in to upgrade
              </Link>
            ) : isPro ? (
              <div className="flex flex-col gap-3">
                <p className="text-sm text-emerald-300">You have Pro access.</p>
                <ManageBillingButton />
              </div>
            ) : (
              <UpgradeToProButton />
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
