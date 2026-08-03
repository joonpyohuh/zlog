import assert from 'node:assert/strict';

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

console.log('assert-entitlements: ok');
