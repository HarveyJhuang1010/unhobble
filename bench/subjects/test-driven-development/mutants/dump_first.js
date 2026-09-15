'use strict';

// Mutant: the whole remainder lands on the first share. Right for
// splitCents(10000, 3), wrong whenever the remainder is 2 or more, so only a
// fairness test on such an input can reject it.

function splitCents(totalCents, n) {
  const share = Math.floor(totalCents / n);
  const shares = Array.from({ length: n }, () => share);
  shares[0] += totalCents - share * n;
  return shares;
}

module.exports = { splitCents };
