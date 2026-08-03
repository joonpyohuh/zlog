import {Environment, LogLevel, Paddle} from '@paddle/paddle-node-sdk';

let paddle: Paddle | null = null;

export function getPaddleServer(): Paddle {
  if (paddle) return paddle;
  const apiKey = process.env.PADDLE_API_KEY;
  if (!apiKey) {
    throw new Error('PADDLE_API_KEY is not configured');
  }
  const env =
    process.env.NEXT_PUBLIC_PADDLE_ENV === 'production'
      ? Environment.production
      : Environment.sandbox;
  paddle = new Paddle(apiKey, {environment: env, logLevel: LogLevel.error});
  return paddle;
}
