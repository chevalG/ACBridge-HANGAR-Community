# Known limitations

- Normal builds hide unfinished FXR and laser-blade runtime controls. The
  experimental research code remains available only through an internal
  environment switch and is not part of the supported public workflow.
- Material and Shader reconstruction is partial. Some surfaces use
  source-backed approximations and will not match the game pixel for pixel.
- A source clip may exist while its skeleton, container stage or additive setup
  is incompatible. HANGAR reports these states separately and does not invent a
  binding.
- Free weapon mounting remains experimental. Weapon-bay folding, the complete
  shoulder animation, IK and combat behavior are not reproduced.
- GLB export contains the model, supported materials and native motion. It does
  not include the experimental FXR runtime.
- The final 3.1.1 package has machine-level end-to-end checks but
  `gui_acceptance=false`: manual visual appearance, playback/seek appearance and
  long-duration GPU sessions were not completed.
- The complete regression run retained 2 failures requiring an absent default
  C4201 body/head layout and 9 setup errors requiring a missing historical H06
  frozen fixture. The two C4201 tests pass with the genuine source layout. No
  failing test was hidden or replaced with fabricated data.
- Game Root extraction depends on the user's AC6 version and local disk speed.
  WitchyBND and .NET 10 Desktop Runtime must be available before the Game Root
  option is enabled.
