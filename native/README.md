# Native tools

`luau/` contains the compiler sources at the revision in [UPSTREAM.json](UPSTREAM.json), including upstream license files.

`luaudec/` contains the patched decompiler. Modified source hashes are specified in `../skills/roblox-client-reverse/assets/luaudec-source-manifest.json`. Patches are also included in the client-reverse skill for its standalone setup procedure. The upstream decompiler revision has no license file; this repository does not assign it a replacement license.

Build with CMake and the Visual Studio 2022 C++ tools on Windows:

```powershell
cmake -S native -B build/native -G "Visual Studio 17 2022" -A x64
cmake --build build/native --config Release --target luaudec rcr-native-tests Luau.Compile.CLI Luau.Repl.CLI --parallel 6
./build/native/Release/rcr-native-tests.exe
```

Alternatively, `rcr.py setup` fetches the pinned dependencies and applies and verifies the included patch.
