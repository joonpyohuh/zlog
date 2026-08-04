import Link from 'next/link';
import {createClient} from '@/lib/supabase/server';
import {isLocalMode} from '@/lib/local-mode.mjs';

export async function SiteHeader() {
  const localMode = isLocalMode();
  const user = localMode ? null : (await (await createClient()).auth.getUser()).data.user;

  return (
    <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-5 py-5">
      <Link href="/" className="brand text-xl lowercase text-white">
        zlog
      </Link>
      <nav className="flex items-center gap-4 text-sm text-neutral-400">
        <Link href="/pricing" className="hover:text-white">
          Pricing
        </Link>
        {localMode ? (
          <Link href="/studio" className="hover:text-white">
            Studio
          </Link>
        ) : user ? (
          <Link href="/settings/billing" className="hover:text-white">
            Billing
          </Link>
        ) : (
          <Link href="/login" className="hover:text-white">
            Sign in
          </Link>
        )}
      </nav>
    </header>
  );
}
