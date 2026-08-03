import Link from 'next/link';
import {redirect} from 'next/navigation';
import {createClient} from '@/lib/supabase/server';
import {requireProAccess} from '@/lib/billing';
import {UpgradeToProButton} from '@/components/billing/UpgradeToProButton';

export default async function StudioPage() {
  const supabase = await createClient();
  const {
    data: {user},
  } = await supabase.auth.getUser();
  if (!user) redirect('/login?next=/studio');

  try {
    await requireProAccess();
  } catch {
    return (
      <main className="mx-auto flex min-h-[70vh] w-full max-w-lg flex-col justify-center px-5">
        <h1 className="text-2xl text-white">Studio locked</h1>
        <p className="mt-2 text-sm text-neutral-500">
          Pro access is required. Entitlement comes from verified Paddle webhooks only.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <UpgradeToProButton />
          <Link href="/settings/billing" className="text-sm text-neutral-400 underline">
            Billing settings
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-5 pb-24 pt-8">
      <h1 className="text-3xl text-white">Studio</h1>
      <p className="mt-2 text-sm text-neutral-500">
        You have Pro access. Point this UI at your long-running zlog API (`ZLOG_API_BASE`) for film
        jobs — billing stays on this Next.js app.
      </p>
      <div className="mt-8 rounded-3xl border border-neutral-800 bg-black/70 p-6 text-sm text-neutral-400">
        Composer / job runner can be wired to the existing FastAPI `server.py` host. Pro gate is
        enforced here via `requireProAccess()`.
      </div>
    </main>
  );
}
