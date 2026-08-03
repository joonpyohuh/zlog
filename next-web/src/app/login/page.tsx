'use client';

import {FormEvent, Suspense, useState} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {createClient} from '@/lib/supabase/client';

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get('next') || '/settings/billing';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setMessage(null);
    const supabase = createClient();
    try {
      if (mode === 'signin') {
        const {error: err} = await supabase.auth.signInWithPassword({email, password});
        if (err) throw err;
        router.push(next);
        router.refresh();
      } else {
        const {error: err} = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
          },
        });
        if (err) throw err;
        setMessage('Check your email to confirm, or sign in if confirmations are disabled.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-[70vh] w-full max-w-md flex-col justify-center px-5">
      <h1 className="text-2xl font-medium text-white">
        {mode === 'signin' ? 'Sign in' : 'Create account'}
      </h1>
      <p className="mt-2 text-sm text-neutral-500">Access billing and Pro film creation.</p>

      <form onSubmit={onSubmit} className="mt-8 flex flex-col gap-3">
        <label className="text-xs uppercase tracking-wide text-neutral-500">
          Email
          <input
            required
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-xl border border-neutral-800 bg-neutral-950 px-3 py-2.5 text-sm text-white outline-none focus:border-neutral-600"
          />
        </label>
        <label className="text-xs uppercase tracking-wide text-neutral-500">
          Password
          <input
            required
            type="password"
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-xl border border-neutral-800 bg-neutral-950 px-3 py-2.5 text-sm text-white outline-none focus:border-neutral-600"
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="mt-2 rounded-full bg-white py-2.5 text-sm font-medium text-black disabled:opacity-50"
        >
          {loading ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Sign up'}
        </button>
      </form>

      <button
        type="button"
        className="mt-4 text-sm text-neutral-400 underline-offset-4 hover:underline"
        onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
      >
        {mode === 'signin' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
      </button>

      {error && (
        <p className="mt-4 text-sm text-red-300" role="alert">
          {error}
        </p>
      )}
      {message && <p className="mt-4 text-sm text-neutral-300">{message}</p>}
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-[70vh] items-center justify-center text-sm text-neutral-400">
          Loading…
        </main>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
