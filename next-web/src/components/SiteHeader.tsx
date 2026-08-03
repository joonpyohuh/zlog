import Link from 'next/link';
import {createClient} from '@/lib/supabase/server';

export async function SiteHeader() {
  const supabase = await createClient();
  const {
    data: {user},
  } = await supabase.auth.getUser();

  return (
    <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-5 py-5">
      <Link href="/" className="brand text-xl lowercase text-white">
        zlog
      </Link>
      <nav className="flex items-center gap-4 text-sm text-neutral-400">
        <Link href="/pricing" className="hover:text-white">
          Pricing
        </Link>
        {user ? (
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
