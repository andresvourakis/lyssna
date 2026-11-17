# Contributing

Thanks for wanting to help. Bug reports and small focused pull requests are
the most useful.

## Setup

You need Python 3.11 or later and [uv](https://docs.astral.sh/uv/).

    uv sync
    make test      # run the tests
    make run       # run the menu bar app from source
    make app       # build Lyssna.app and the release zip

Run `make` to list every target. Running from source, macOS asks for
Accessibility access for your terminal app, not for the project.

## Before you open a pull request

- `make test` passes.
- `uvx ruff check lyssna tests` is clean.
- One change per pull request, with a short note on how you tested it.
- UI changes: include a screenshot or a short screen recording.

## Adding a voice provider

Each provider is one small class in `lyssna/providers/`. Copy an existing one,
set the class attributes (name, label, vendor, key_url, price_per_million),
implement `synthesize`, and register it in `lyssna/providers/__init__.py`.
Send requests through `Provider._post`: providers must set a request timeout and
must not retry automatically, because a retry bills the user twice. Read keys
through `self.api_key`, which looks in the Keychain first; never log a key.

## Reporting bugs

Open an issue at https://github.com/andresvourakis/lyssna/issues. Include your
macOS version, Mac type (Apple silicon or Intel), the voice provider you use,
and the relevant lines from the log (menu bar icon, then **Settings > Open
Log**). The log does not contain your text or the voice provider's error
messages, but it does contain file paths with your user name; check it before
you paste it.

Security problems: see [SECURITY.md](SECURITY.md).

## License

Lyssna is licensed under GPL-3.0-or-later. By contributing, you agree that
your contribution is licensed under the same terms.
