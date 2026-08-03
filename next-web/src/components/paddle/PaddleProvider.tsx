'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {initializePaddle, type Paddle} from '@paddle/paddle-js';

type OpenCheckoutArgs = {
  priceId: string;
  email?: string | null;
  userId: string;
};

type PaddleContextValue = {
  ready: boolean;
  error: string | null;
  openCheckout: (args: OpenCheckoutArgs) => Promise<void>;
};

const PaddleContext = createContext<PaddleContextValue | null>(null);

function initialPaddleError(): string | null {
  return process.env.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN ? null : 'Paddle is not configured';
}

export function PaddleProvider({children}: {children: ReactNode}) {
  const paddleRef = useRef<Paddle | undefined>(undefined);
  const initStarted = useRef(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(initialPaddleError);

  useEffect(() => {
    if (initStarted.current) return;
    initStarted.current = true;

    const token = process.env.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN;
    if (!token) return;

    const environment =
      process.env.NEXT_PUBLIC_PADDLE_ENV === 'production' ? 'production' : 'sandbox';

    void initializePaddle({
      token,
      environment,
    })
      .then((instance) => {
        if (!instance) {
          setError('Paddle failed to initialize');
          return;
        }
        paddleRef.current = instance;
        setReady(true);
      })
      .catch(() => {
        setError('Paddle failed to initialize');
      });
  }, []);

  const openCheckout = useCallback(
    async ({priceId, email, userId}: OpenCheckoutArgs) => {
      const paddle = paddleRef.current;
      if (!paddle) {
        throw new Error(error ?? 'Paddle is unavailable');
      }
      if (!priceId) {
        throw new Error('Missing Pro price ID');
      }

      paddle.Checkout.open({
        items: [{priceId, quantity: 1}],
        customer: email ? {email} : undefined,
        customData: {
          user_id: userId,
          plan: 'pro',
          source: 'zlog_web',
        },
        settings: {
          displayMode: 'overlay',
          theme: 'dark',
          successUrl: `${window.location.origin}/settings/billing?checkout=success`,
        },
      });
    },
    [error],
  );

  const value = useMemo(
    () => ({ready, error, openCheckout}),
    [ready, error, openCheckout],
  );

  return <PaddleContext.Provider value={value}>{children}</PaddleContext.Provider>;
}

export function usePaddle() {
  const ctx = useContext(PaddleContext);
  if (!ctx) {
    throw new Error('usePaddle must be used within PaddleProvider');
  }
  return ctx;
}
