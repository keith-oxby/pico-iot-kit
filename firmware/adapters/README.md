# Adding a new backend

1. Create `firmware/adapters/your_backend.py` implementing `DownstreamAdapter`
   (see `base.py`): `connect()`, `publish()`, `poll_commands()`, and
   optionally `announce()`.
2. Register it in `firmware/adapters/__init__.py`'s `BACKEND_TYPES` dict.
3. Add it to the setup form's backend-type list (`firmware/provisioning/templates/`)
   with whatever fields it needs (host/port/credentials, a connection
   string, etc.) so users can select it without editing code.
4. Default to TLS/encrypted transport -- see `docs/security.md`. Plaintext
   is an explicit opt-in only, never the default.
5. Add a test under `tests/test_adapters/`.

AWS IoT is the documented first candidate for a post-v1 addition. See
`docs/adding-a-backend.md` for the full contributor guide.
