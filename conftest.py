"""Root pytest configuration.

`pytester` is enabled here because a top-level conftest is the only supported place
to declare plugins; `tests/test_pytest_policy.py` uses it to *behaviourally* prove
that the committed configuration deselects `live` tests, rather than only asserting
that the settings look right.
"""

pytest_plugins = ["pytester"]
