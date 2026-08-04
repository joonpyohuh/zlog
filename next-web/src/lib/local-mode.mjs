export function isLocalMode(env = process.env) {
  return env.NODE_ENV !== 'production' && env.ZLOG_DEV_MODE === '1';
}
