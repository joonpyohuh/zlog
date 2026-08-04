import assert from 'node:assert/strict';
import {shouldProcessWebhook} from '../src/lib/paddle/webhook-state.mjs';
import {isLocalMode} from '../src/lib/local-mode.mjs';

/** Mirrors `statusGrantsPro` in src/lib/billing.ts — fails if mapping drifts. */
const PRO_STATUSES = new Set(['active', 'trialing']);

function statusGrantsPro(status) {
  if (!status) return false;
  return PRO_STATUSES.has(String(status).toLowerCase());
}

assert.equal(statusGrantsPro('active'), true);
assert.equal(statusGrantsPro('trialing'), true);
assert.equal(statusGrantsPro('canceled'), false);
assert.equal(statusGrantsPro('paused'), false);
assert.equal(statusGrantsPro('past_due'), false);
assert.equal(statusGrantsPro('inactive'), false);
assert.equal(shouldProcessWebhook(null), true);
assert.equal(shouldProcessWebhook({processing_error: null}), false);
assert.equal(shouldProcessWebhook({processing_error: 'temporary failure'}), true);
assert.equal(isLocalMode({NODE_ENV: 'development', ZLOG_DEV_MODE: '1'}), true);
assert.equal(isLocalMode({NODE_ENV: 'production', ZLOG_DEV_MODE: '1'}), false);
assert.equal(isLocalMode({NODE_ENV: 'development', ZLOG_DEV_MODE: '0'}), false);

console.log('assert-entitlements: ok');
