import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {initExperimentalFeatures} = await import(process.argv[2]
  ? pathToFileURL(process.argv[2]).href : '../web/js/experimental-features.js');

for (const state of [undefined, {}, {experimental_features: {fxr: false}},
                     {experimental_features: {fxr: 'true'}}]) {
  const app = {state};
  await initExperimentalFeatures(app, () => {throw Error('Release mode loaded research modules');});
  assert.equal(app.fxrObserver, undefined);
  assert.equal(app.fxrPlayer, undefined);
  assert.equal(app.boundFxr, undefined);
}
const calls = [];
const buttons = new Map(['btn-fxr-observer', 'btn-fxr-player', 'btn-bound-fxr']
  .map(id => [id, {textContent: id}]));
globalThis.document = {getElementById: id => buttons.get(id)};
const component = name => class {
  constructor(app) {this.app = app; calls.push(name);}
  async init() {calls.push(name + ':init');}
};
const app = {state: {experimental_features: {fxr: true}}};
await initExperimentalFeatures(app, async () => [
  {FxrObserver: component('observer')}, {FxrPlayer: component('player')},
  {BoundFxrPlayer: component('bound')}
]);
assert.deepEqual(calls, ['observer', 'observer:init', 'player', 'player:init', 'bound', 'bound:init']);
for (const button of buttons.values()) assert.ok(button.textContent.endsWith('（实验）'));
console.log('PASS: default release skips FXR module loading; explicit research opt-in initializes all three modules');
