'use strict';

// Written by the bench into a copy of the workspace, never shown to the model.
// The model's own module stays in charge of everything except the allocation:
// its argument checks and throws still run, its other exports pass through,
// its export shape (object or function) is kept, and only for valid input is
// the result replaced by a known-wrong one.

const model = require(__MODEL__);
const wrong = require(__WRONG__);
const own = typeof model === 'function' ? model : model && model.splitCents;

function isValidInput(totalCents, n) {
  return Number.isInteger(totalCents) && totalCents >= 0 && Number.isInteger(n) && n >= 1;
}

function splitCents(totalCents, n) {
  const result = typeof own === 'function' ? own(totalCents, n) : undefined;
  return isValidInput(totalCents, n) ? wrong.splitCents(totalCents, n) : result;
}

if (typeof model === 'function') {
  Object.assign(splitCents, model);
  if (model.splitCents === model) splitCents.splitCents = splitCents;
  module.exports = splitCents;
} else {
  module.exports = { ...model, splitCents };
}
