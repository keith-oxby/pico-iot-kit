"""The Application Runtime package.

Deliberately empty of imports. Importing `firmware.runtime.<module>` must not
pull in the rest of the runtime: the provisioning layer uses
`runtime.config_schema` and `runtime.ca_store`, the adapters use
`runtime.clock` and `runtime.ca_store`, and the runtime's `device` module
imports the adapter registry. Importing `device` here would make those a
cycle (and load the whole runtime, adapters and sensors during setup mode).
Import what you need from its module, e.g. `from firmware.runtime.device import run`.
"""
