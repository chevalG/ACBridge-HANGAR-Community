# Third-party notices

The HANGAR 3.1.1 Windows package consists of ACBridge work plus the components
listed here. Exact upstream repository revisions and license identities were
reviewed for the public release.

| Component | Version / revision | License | Distribution and source |
|---|---|---|---|
| ACBridge HANGAR and ACBridge Toolkit overlay | 3.1.1 / `ed257be4a64b98e2baea4fcd190075871b2e8cf7` baseline | GPL-3.0-only | Public HANGAR source repository and matching source release |
| ACBridge Toolkit frozen base | 0.5.1a5 | GPL-3.0-only | `licenses/acbridge_toolkit-0.5.1a5.tar.gz` |
| NuxeBridge | 1.0.0 | GPL-3.0-only | `third_party/nuxebridge` in public source and source release |
| Nuxe | 1.2.1, `16d94fcd379f45721047a0c2b7b743e84b4fa08b` | GPL-3.0-or-later | Bundled runtime; source linked from the public source manifest |
| Coremats | NuGet `0.0.0-2026.8.1.1.34.26`, `a315f5bf7569945cbbd39fb902718de765dba4c4` | GPL-3.0-or-later | Bundled runtime; source linked from the public source manifest |
| Bex | 0.0.1, `bb1ade180a72465f89103880f4f5bef500e64236` | GPL-3.0-or-later | Bundled runtime; source linked from the public source manifest |
| BinderKeys AC6 data | `b95a32c509e35e5b18306c1d37a7778ea4034f38` | Apache-2.0 | Bundled index, including upstream `LICENSE` |
| BouncyCastle.Cryptography | 2.7.0 | MIT | Bundled dependency; upstream source URL in package metadata |
| ZstdSharp.Port | 0.8.8 | MIT | Bundled dependency; upstream source URL in package metadata |
| CommunityToolkit.Mvvm | 8.4.2 | MIT | Bundled dependency; upstream source URL in package metadata |
| Python | 3.14.3 | PSF and bundled component notices | Bundled runtime; `licenses/Python-LICENSE.txt` |
| NumPy | 2.5.4 | BSD-3-Clause and bundled notices | Bundled runtime |
| SciPy | 1.18.1 | BSD-3-Clause and bundled notices | Bundled runtime |
| Pillow | 13.0.0 | HPND and bundled notices | Bundled runtime |
| Node.js | 24.14.0 | MIT and bundled third-party notices | `licenses/Node-LICENSE.txt` |
| Three.js | 0.180.0 | MIT | `web/vendor/three/LICENSE` |
| Khronos glTF Validator | packaged version | Apache-2.0 | `licenses/gltf-validator-*` |

WitchyBND is not part of the distribution. The optional setup assistant points
to the official upstream release `3.0.1.0`, verifies the Windows archive hash
`a3e6b2a0f7eac13f5e83b6602a1149322439c0662baa140ecdd84be28af50364`,
and lets the user place it in HANGAR's runtime tool directory. Users are
responsible for reviewing the upstream distribution and its Oodle terms.
