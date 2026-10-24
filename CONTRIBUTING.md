# Contributing

Issues and pull requests are welcome.

- Run `python -m pytest` and `ruff check .` before opening a pull request. Both
  must pass.
- New behaviour comes with a test. The suite is offline: stub the network and
  the database the way the existing tests do.
- Keep secrets out of the repository. `.env` is ignored; add new settings to
  `.env.example` by name only.
- A change to the scoring rubric changes every future rating. Explain in the
  pull request what it changes and why.
