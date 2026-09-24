# Samples

Two worked examples of Enodia's whole flow, each in a folder of its own with the commands that
regenerate every file in it, and `tests/test_samples.py` holding each file to what those
commands produce.

- [`synthetic_montevideo/`](synthetic_montevideo/README.md): real corners and street geometry of
  Avenida Rivera in Montevideo, and radios invented by `make_rivera.py`. It demonstrates the
  workflow, not measured accuracy.
- [`dolores/`](dolores/README.md): a real outing, walked in Dolores on 2026-09-22, with the
  networks pseudonymised by `--export-public --keep-places --keep-time`.
