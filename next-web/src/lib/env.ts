function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`Missing environment variable: ${name}`);
  }
  return value;
}

export function getPublicEnv() {
  return {
    appUrl: process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000',
    supabaseUrl: required('NEXT_PUBLIC_SUPABASE_URL', process.env.NEXT_PUBLIC_SUPABASE_URL),
    supabaseAnonKey: required(
      'NEXT_PUBLIC_SUPABASE_ANON_KEY',
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
    ),
    paddleEnv: (process.env.NEXT_PUBLIC_PADDLE_ENV ?? 'sandbox') as 'sandbox' | 'production',
    paddleClientToken: process.env.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN ?? '',
    paddleProPriceId: process.env.NEXT_PUBLIC_PADDLE_PRO_PRICE_ID ?? '',
  };
}

/** Client-safe Paddle public config (no secrets). */
export function getPublicPaddleEnv() {
  const env = (process.env.NEXT_PUBLIC_PADDLE_ENV ?? 'sandbox') as 'sandbox' | 'production';
  const clientToken = process.env.NEXT_PUBLIC_PADDLE_CLIENT_TOKEN ?? '';
  if (!clientToken) {
    throw new Error('Missing NEXT_PUBLIC_PADDLE_CLIENT_TOKEN');
  }
  return {env, clientToken};
}

export function getServerEnv() {
  const pub = getPublicEnv();
  return {
    ...pub,
    supabaseServiceRoleKey: required(
      'SUPABASE_SERVICE_ROLE_KEY',
      process.env.SUPABASE_SERVICE_ROLE_KEY,
    ),
    paddleApiKey: required('PADDLE_API_KEY', process.env.PADDLE_API_KEY),
    paddleWebhookSecret: required('PADDLE_WEBHOOK_SECRET', process.env.PADDLE_WEBHOOK_SECRET),
    zlogApiBase: process.env.ZLOG_API_BASE ?? '',
  };
}
