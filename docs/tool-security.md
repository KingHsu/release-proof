# Read-only tool security

## Command boundary

The local Git adapter permits only `rev-parse`, `diff`, `show`, and fixed-string `grep`. It invokes `subprocess.run` with an argument list, `shell=False`, a timeout, and bounded output. It never executes repository scripts or model-generated commands.

The planner can propose only a structured action; it cannot call an adapter. Main-path operations are admitted through `EvidenceToolHarness` and `ReadOnlyToolRegistry` with an explicit five-tool allowlist, typed arguments, frozen refs, changed-file/report manifests, and a stable action key. Persistent State rejects duplicates and stops further collection at configured model-call, tool-call, planner-step, wall-clock, or no-progress limits. A tool error or rejected proposal is traced and never converted into positive evidence.

## Filesystem boundary

- resolve an explicit repository root;
- reject absolute paths, null bytes, `..`, and `.git` reads;
- reject symlinks and resolved paths outside the root;
- allow only text/source/report extensions;
- block `.env`, key files, credentials, and package-auth files;
- cap file and report sizes;
- require test and CI reports to be inside the repository.

`--requirement-file` is a distinct user/host input rather than a model-selectable repository tool. The CLI accepts only a bounded regular UTF-8 Markdown/plain-text file, blocks symlinks and secret-like names, rejects NUL bytes, and records a basename/content-hash locator rather than the absolute host path.

## Output handling

Tool output is truncated and patterns resembling Authorization, tokens, API keys, or private keys are replaced. Errors expose a category and a bounded sanitized excerpt rather than environment variables or arbitrary absolute paths.

## Why tests are not executed

Running an unfamiliar repository is code execution. P0 reads reports that the repository owner has already produced. A future trusted-test profile must be preconfigured by the maintainer; the model cannot edit its command, working directory, network policy, timeout, or environment.

