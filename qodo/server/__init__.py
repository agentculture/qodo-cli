"""The HTTP API (``qodo-cli serve``): routes generated from the verb registry.

FastAPI and uvicorn are the optional ``server`` extra and are imported lazily, inside
:func:`qodo.server.app.create_app` and :mod:`qodo.server.serve` only; importing this package
needs nothing but the standard library. App-specific wiring lives here; the generic
authentication framework is :mod:`qodo.bridge`.
"""
