# Desktop entry measurements — 3.1.2

Measured on the clean extracted candidate, Windows 11 x64, warm disk cache.

| Metric | Observed |
|---|---|
| Launcher to service state file | ~1.1-1.2 s |
| HANGAR.exe working set (window open) | ~60 MB |
| HANGAR.exe private memory | ~38 MB |
| Python service working set | ~32 MB |
| Extra runtime | WebView2 (Edge runtime already installed) |
| Launcher binary | ~245 KB |

The desktop shell adds one small native process and uses the existing WebView2
runtime instead of bundling a browser. The Python service and its engine
footprint are unchanged from 3.1.1.
