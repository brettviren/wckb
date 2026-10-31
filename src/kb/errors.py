"""Exceptions that the CLI reports as a one-line message rather than a traceback."""


class KBError(Exception):
    """A user-facing failure: bad input, missing record, unavailable tool."""
