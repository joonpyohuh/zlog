import Link from 'next/link';
import {createClient} from '@/lib/supabase/server';
import {userHasProAccess} from '@/lib/billing';
import {UpgradeToProButton} from '@/components/billing/UpgradeToProButton';

export default async function HomePage() {
  const supabase = await createClient();
  const {
    data: {user},
  } = await supabase.auth.getUser();
  const isPro = user ? await userHasProAccess(user.id) : false;

  return (
    <main className="mx-auto flex min-h-[75vh] w-full max-w-3xl flex-col items-center justify-center px-5 pb-24 text-center">
      <h1 className="brand text-[clamp(72px,15vw,128px)] lowercase leading-none text-white">
        zlog
      </h1>
      <p className="mt-5 text-xs font-medium uppercase tracking-[0.22em] text-neutral-500">
        Photo. Video. Words. Film.
      </p>

      <div className="mt-12 w-full max-w-xl rounded-[28px] border border-neutral-800 bg-black/80 px-4 py-4 text-left shadow-2xl">
        <p className="text-sm text-neutral-400">
          Drop photos, video, or write a note — then generate a short film.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {user ? (
            isPro ? (
              <Link
                href="/studio"
                className="rounded-full bg-white px-5 py-2.5 text-sm font-medium text-black"
              >
                Open studio
              </Link>
            ) : (
              <>
                <UpgradeToProButton label="Unlock Pro to create" />
                <Link href="/pricing" className="text-sm text-neutral-400 underline-offset-4 hover:underline">
                  See plans
                </Link>
              </>
            )
          ) : (
            <Link
              href="/login?next=/"
              className="rounded-full bg-white px-5 py-2.5 text-sm font-medium text-black"
            >
              Sign in to start
            </Link>
          )}
        </div>
        {!isPro && (
          <p className="mt-3 text-xs text-neutral-600">
            Film rendering runs on your zlog API host. Pro unlocks creation in this app.
          </p>
        )}
      </div>
    </main>
  );
}
