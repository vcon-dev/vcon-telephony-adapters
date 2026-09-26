"""Skip this directory from the default test run.

Everything under tests/e2e/freeswitch/ is a manual harness (shell scripts, a
mock conserver, a FreeSWITCH Lua/dialplan config set) for exercising the
FreeSWITCH adapter against a real, locally running FreeSWITCH instance. None
of it is pytest-collectible test code, and it needs services (FreeSWITCH,
Redis, this repo's own adapter) that CI does not have, so it is skipped
unconditionally rather than gated on a marker.

To run it: see the README in this directory.
"""

# Standard pytest hook: excludes every file in this directory from collection.
collect_ignore_glob = ["*"]
