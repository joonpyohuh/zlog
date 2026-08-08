import Link from 'next/link';
import {redirect} from 'next/navigation';
import {createClient} from '@/lib/supabase/server';
import {requireProAccess} from '@/lib/billing';
import {UpgradeToProButton} from '@/components/billing/UpgradeToProButton';
import {StudioComposer} from '@/components/studio/StudioComposer';
import {isLocalMode} from '@/lib/local-mode.mjs';

export default async function StudioPage({
  searchParams,
}: {
  searchParams: Promise<{job?: string}>;
}) {
  const {job} = await searchParams;
  const localMode = isLocalMode();
  if (!localMode) {
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
  }

  const apiBase = process.env.ZLOG_API_BASE ?? process.env.NEXT_PUBLIC_ZLOG_API_BASE ?? '';

  return (
    <main className="mx-auto w-full max-w-3xl px-5 pb-24 pt-8">
      <h1 className="brand text-4xl text-white">zlog</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Personalized travel editing · separate long and short films
      </p>
      {!apiBase ? (
        <p className="mt-6 rounded-2xl border border-amber-900/40 bg-amber-950/20 p-4 text-sm text-amber-100/80">
          Set `ZLOG_API_BASE` (or `NEXT_PUBLIC_ZLOG_API_BASE`) to your local uvicorn host, e.g.
          `http://127.0.0.1:8000`.
        </p>
      ) : null}
      <StudioComposer apiBase={apiBase} devModeDefault={localMode} initialJobId={job} />
    </main>
  );
}
