'use client';

import {Suspense, useEffect, useState} from 'react';
import {useSearchParams} from 'next/navigation';
import {initializePaddle, type Paddle} from '@paddle/paddle-js';
import {getPublicPaddleEnv} from '@/lib/env';

function CheckoutInner() {
  const params = useSearchParams();
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const {env, clientToken} = getPublicPaddleEnv();
        const paddle = await initializePaddle({
          environment: env === 'sandbox' ? 'sandbox' : 'production',
          token: clientToken,
        });
        if (cancelled) return;
        if (!paddle) {
          setStatus('error');
          setError('Paddle failed to initialize.');
          return;
        }
        // Paddle payment-link / overlay query params are handled by Paddle.js when present.
        const _txn = params.get('_ptxn');
        if (_txn) {
          (paddle as Paddle).Checkout.open({transactionId: _txn});
        }
        setStatus('ready');
      } catch (err) {
        if (!cancelled) {
          setStatus('error');
          setError(err instanceof Error ? err.message : 'Checkout unavailable');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params]);

  return (
    <main className="mx-auto flex min-h-[70vh] w-full max-w-lg flex-col items-center justify-center px-5 text-center">
      <h1 className="brand text-4xl lowercase text-white">zlog</h1>
      <p className="mt-3 text-sm text-neutral-500">Secure checkout powered by Paddle</p>
      {status === 'loading' && (
        <p className="mt-8 text-sm text-neutral-400" aria-live="polite">
          Loading checkout…
        </p>
      )}
      {status === 'ready' && !params.get('_ptxn') && (
        <p className="mt-8 text-sm text-neutral-400">
          Open upgrade from Pricing or Billing to start checkout. This page is also the default
          Paddle payment link landing page.
        </p>
      )}
      {error && (
        <p className="mt-8 text-sm text-red-300" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}

export default function CheckoutPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-[70vh] items-center justify-center text-sm text-neutral-400">
          Loading checkout…
        </main>
      }
    >
      <CheckoutInner />
    </Suspense>
  );
}
