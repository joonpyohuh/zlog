'use client';

import {useState} from 'react';
import {useRouter} from 'next/navigation';
import {createClient} from '@/lib/supabase/client';
import {usePaddle} from '@/components/paddle/PaddleProvider';

type Props = {
  className?: string;
  label?: string;
};

export function UpgradeToProButton({className, label = 'Upgrade to Pro'}: Props) {
  const {openCheckout, ready, error: paddleError} = usePaddle();
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    if (loading) return;
    setError(null);
    setLoading(true);
    try {
      const supabase = createClient();
      const {
        data: {user},
      } = await supabase.auth.getUser();
      if (!user) {
        router.push('/login?next=/pricing');
        return;
      }
      if (!ready) {
        throw new Error(paddleError ?? 'Paddle is still loading');
      }
      const priceId = process.env.NEXT_PUBLIC_PADDLE_PRO_PRICE_ID;
      if (!priceId) {
        throw new Error('Pro price is not configured');
      }
      await openCheckout({
        priceId,
        email: user.email,
        userId: user.id,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not open checkout');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="inline-flex flex-col items-stretch gap-2">
      <button
        type="button"
        aria-label={label}
        disabled={loading || Boolean(paddleError)}
        onClick={() => void onClick()}
        className={
          className ??
          'rounded-full bg-white px-5 py-2.5 text-sm font-medium text-black transition hover:bg-neutral-200 disabled:cursor-not-allowed disabled:opacity-50'
        }
      >
        {loading ? 'Opening checkout…' : label}
      </button>
      {(error || paddleError) && (
        <p className="text-center text-xs text-neutral-400" role="alert">
          {error || paddleError}
        </p>
      )}
    </div>
  );
}
