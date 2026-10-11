# Contributing to BetterChat

Thanks for helping improve the Nexo engine and its public toolkit. Focused fixes,
reproducible bug reports, client integration evidence and documentation are welcome.

## Development setup

Python 3.11+ and Git are required. Runtime tests use the standard library;
no provider account or API key is needed.

```shell
git clone https://github.com/Israelcodigo/betterchat.git
cd betterchat
python -B -m unittest discover -s tests -v
python -B examples/demo.py
python -B scripts/release_audit.py
```

The demo retains its temporary repository so you can inspect its check receipts.
Do not run authenticated provider probes as part of ordinary offline tests.

## Send a change

1. Describe the observable problem and the expected behavior.
2. Keep the change focused. Add a behavior test for runtime changes or regressions.
3. Run the relevant checks and report their results in the pull request.
4. Distinguish fixture processes from authenticated model runs. Document anything
   not verified, including platform or provider requirements.

Keep native client permissions, finite runner limits, and the distinction between
`review` and `done`. Do not weaken checks to complete a task.

## Build distributions

```shell
python -m pip install build
python -m build
python scripts/package_release.py
```

The public inventory is defined in `scripts/package_release.py`. Add new public
assets there when needed, and preserve the hash-bound live evidence bytes.

## Useful contributions

- Real Claude Code and Gemini CLI integration runs with sanitized evidence.
- Cross-platform compatibility fixes and regression tests.
- English translations of the technical manuals.
- Comparative evaluations with equal tasks, budgets and independent checks.

Never include credentials, private prompts, personal profiles or the `.nexo/`
directory in a contribution. See [security guidance](SECURITY.md).
