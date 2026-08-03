'use client';

import {useState} from 'react';

type Props = {
  className?: string;
};

export function ManageBillingButton({className}: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    if (loading) return;
    setError(null);
    setLoading(true);
    try {
      const res = await fetch('/api/paddle/customer-portal', {method: 'POST'});
      const data = (await res.json()) as {url?: string; error?: string};
      if (!res.ok || !data.url) {
        throw new Error(data.error || 'Could not open billing portal');
      }
      window.location.href = data.url;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Billing portal unavailable');
      setLoading(false);
    }
  }

  return (
    <div className="inline-flex flex-col gap-2">
      <button
        type="button"
        aria-label="Manage billing"
        disabled={loading}
        onClick={() => void onClick()}
        className={
          className ??
          'rounded-full border border-neutral-700 px-5 py-2.5 text-sm text-white transition hover:border-neutral-500 disabled:opacity-50'
        }
      >
        {loading ? 'Opening…' : 'Manage billing'}
      </button>
      {error && (
        <p className="text-xs text-neutral-400" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
