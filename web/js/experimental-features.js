/** One release gate: no FXR downloads, controls or frame work until opted in. */
export async function initExperimentalFeatures(app, load = () => Promise.all([
  import('./fxr-observer.js'), import('./fxr-player.js'), import('./fxr-bound.js')
])) {
  if (app.state?.experimental_features?.fxr !== true) return;
  const [{FxrObserver}, {FxrPlayer}, {BoundFxrPlayer}] = await load();
  app.fxrObserver = new FxrObserver(app);
  await app.fxrObserver.init();
  app.fxrPlayer = new FxrPlayer(app);
  await app.fxrPlayer.init();
  app.boundFxr = new BoundFxrPlayer(app);
  await app.boundFxr.init();
  for (const id of ['btn-fxr-observer', 'btn-fxr-player', 'btn-bound-fxr']) {
    const button = document.getElementById(id);
    if (button) button.textContent += '（实验）';
  }
}
