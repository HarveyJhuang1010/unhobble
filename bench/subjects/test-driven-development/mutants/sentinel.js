'use strict';

// Probe, not a plausible bug: every share is -1. If the model's tests do not
// fail more under this than under the model's own code, they never reach the
// allocation through src/split.js, and the mutation checks cannot judge them.

function splitCents(totalCents, n) {
  return Array.from({ length: n }, () => -1);
}

module.exports = { splitCents };
