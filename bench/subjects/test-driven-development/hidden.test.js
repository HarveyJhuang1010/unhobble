'use strict';

// Hidden acceptance test. Copied in only after the model has finished, so the
// model never sees it. BENCH_MODULE is the absolute path of the implementation
// under test (the workspace's src/split.js).

const assert = require('node:assert/strict');
const test = require('node:test');

const target = process.env.BENCH_MODULE;
if (!target) {
  throw new Error('BENCH_MODULE must be the absolute path of the implementation under test');
}
const { splitCents } = require(target);

test('BUG.md: the lost cent goes to the first share', () => {
  assert.deepEqual(splitCents(10000, 3), [3334, 3333, 3333]);
});

test('README example: leftover cents go to the earliest shares, one each', () => {
  assert.deepEqual(splitCents(100, 7), [15, 15, 14, 14, 14, 14, 14]);
});

test('both invariants hold for every total 0..300 and n 1..9', () => {
  for (let total = 0; total <= 300; total++) {
    for (let n = 1; n <= 9; n++) {
      const shares = splitCents(total, n);
      const label = `splitCents(${total}, ${n}) = ${JSON.stringify(shares)}`;
      assert.ok(Array.isArray(shares) && shares.length === n, `${label}: expected ${n} shares`);
      assert.ok(shares.every(Number.isInteger), `${label}: shares must be integer cents`);
      assert.equal(shares.reduce((a, b) => a + b, 0), total, `${label}: exactness`);
      assert.ok(Math.max(...shares) - Math.min(...shares) <= 1, `${label}: fairness`);
      assert.ok(shares.every((s, i) => i === 0 || shares[i - 1] >= s), `${label}: earliest shares first`);
    }
  }
});
