"""Test package.

Two things are set up here, both to stop the suite touching anything real.

``PYKANTUI_HOME`` is pointed at a throwaway directory for the whole run. Any
test that forgets to sandbox itself lands there instead of in the user's actual
board — which is not hypothetical: a collapse test once rewrote a real
``config.json`` because ``save()`` fell back to the default path.

``IsolatedAsyncioTestCase`` also turns on asyncio debug mode, which logs a
warning for every callback slower than 100ms. Booting a Textual app trips that
constantly and buries the results, so the asyncio logger is quietened.
"""

import atexit
import logging
import os
import tempfile

logging.getLogger("asyncio").setLevel(logging.ERROR)

_SANDBOX = tempfile.mkdtemp(prefix="pykantui-tests-")
os.environ.setdefault("PYKANTUI_HOME", _SANDBOX)


@atexit.register
def _cleanup() -> None:
    import shutil

    shutil.rmtree(_SANDBOX, ignore_errors=True)
