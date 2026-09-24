---
paths:
  - "tests/**"
---

A test that builds a result object by hand must use a combination the producer can actually emit -- stubs of impossible states (e.g. successful synthesis with `installed_path=None`) hid the install silent-success bug and an uninstall reporting bug.
Green is not reviewed, especially for uninstall and exit codes: a fix written against one reproduction tends to close it and not its class, so ask which neighbouring paths reach the same code, and where two places encode one rule collapse them rather than patching both.
