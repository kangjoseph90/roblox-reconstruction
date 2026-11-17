# Development guidelines

- The repository ships one skill, roblox-client-reverse: client collection, bytecode recovery, CFG verification and mechanical checks of analysis documents. Game interpretation is done by an agent reading the recovered repository; keep checks mechanical and keep interpretation out of the CLI.
- Keep the skill installable on its own: its CLI and resources use paths relative to the skill directory.
- Preserve pinned upstream revisions, patches, native source hashes and license notices. Rebuild the native tools when changing C++ sources.
- Follow the skill's installation guide for test prerequisites. CI (`.github/workflows/tests.yml`) builds the pinned tools and runs the unit and authored semantic suites with them on Linux and Windows.
- Keep syntax validity, function CFG evidence, module CFG evidence and observed behavior distinct. Do not broaden a validation claim beyond its recorded scope.
- Documentation must describe the software and its usage. Do not include personal machine configuration or operational notes.
