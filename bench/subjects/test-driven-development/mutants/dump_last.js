'use strict';

// Mutant: the whole remainder lands on the last share. This is the one-liner
// eval 2 dictates; it keeps the sum exact but breaks the README's fairness rule.

function splitCents(totalCents, n) {
  const share = Math.floor(totalCents / n);
  const shares = Array.from({ length: n }, () => share);
  shares[n - 1] += totalCents - share * n;
  return shares;
}

module.exports = { splitCents };
