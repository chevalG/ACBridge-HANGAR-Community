# Performance summary

These measurements compare H23 3.1.0 with H24 3.1.1 under the same isolated
profile. They are backend measurements, not claims about browser frame rate or
GPU stability.

| Workload | H23 | H24 |
|---|---:|---:|
| Repeated assembly preflight, median | 5,035.3 ms | 122.0 ms |
| Preflight after index expiry, median | 4,876.2 ms | 2,679.4 ms |
| Repeated full-library snapshot, median | 118.9 ms | 36.8 ms |
| Snapshot plus JSON serialization, median | 149.2 ms | 66.8 ms |

The repeated snapshot keeps the same 268 assets and 5,236 motion records.
Content, revision and relevant file identities invalidate the cache. The first
snapshot costs roughly 33 ms more than the uncached version; the benefit is on
repeated navigation.

Resource conversion results retained equivalent accessor data, materials and
texture bytes in the checked samples. No model precision, texture budget or
animation samples were reduced to create these measurements.

Limits: OS file-cache state and desktop activity were not fully controlled.
The short backend soak does not establish multi-hour GPU or browser stability,
and no manual frame-time trace was available. Performance work stopped at this
point because further speculative frontend changes lacked reproducible visual
evidence.
