"""Reusable service framework: auth, route policy, static serving and cache policy.

Cited from culture-rules (see each module's header for its source path and commit). Nothing
here knows the app that hosts it: the environment prefix, token prefix, dev header and web build
directory are parameters, and the route-to-role table is filled in through ``policy.RoutePolicy``.
Standard library only, except ``static`` (the ``server`` extra, imported lazily).
"""
