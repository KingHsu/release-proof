# Read-only tool security

## Command boundary

The local Git adapter permits only `rev-parse`, `diff`, `show`, and fixed-string `grep`. It invokes `subprocess.run` with an argument list, `shell=False`, a timeout, and bounded output. It never executes repository scripts or model-generated commands.

## Filesystem boundary

- resolve an explicit repository root;
- reject absolute paths, null bytes, `..`, and `.git` reads;
- reject symlinks and resolved paths outside the root;
- allow only text/source/report extensions;
- block `.env`, key files, credentials, and package-auth files;
- cap file and report sizes;
- require test and CI reports to be inside the repository.

## Output handling

Tool output is truncated and patterns resembling Authorization, tokens, API keys, or private keys are replaced. Errors expose a category and a bounded sanitized excerpt rather than environment variables or arbitrary absolute paths.

## Why tests are not executed

Running an unfamiliar repository is code execution. P0 reads reports that the repository owner has already produced. A future trusted-test profile must be preconfigured by the maintainer; the model cannot edit its command, working directory, network policy, timeout, or environment.

