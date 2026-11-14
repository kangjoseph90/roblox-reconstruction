# Development guidelines

- Keep the two skills independently installable. Shared integration changes must preserve their standalone CLIs and relative resource paths.
- Client collection, bytecode recovery and CFG verification belong to roblox-client-reverse. Source interpretation and specification generation belong to roblox-game-reconstruction.
- Preserve pinned upstream revisions, patches, native source hashes and license notices. Rebuild the native tools when changing C++ sources.
- Run the two Python test suites in separate processes to avoid module-name collisions. Follow each skill's installation guide for optional test prerequisites.
- Keep syntax validity, function CFG evidence, module CFG evidence and observed behavior distinct. Do not broaden a validation claim beyond its recorded scope.
- Documentation must describe the software and its usage. Do not include personal machine configuration or operational notes.
