"""The workspace: a tracker's board as ordinary files in a git repository.

    <workspace>/<provider>/projects/<owner?>/<project>/<column>/<key>.md

* :mod:`~pykantui.workspace.layout` -- where everything sits, and the rule that
  a file's directory is the truth about which column its card is in.
* :mod:`~pykantui.workspace.markdown` -- one issue as one file, and back. The
  notes marker is what makes editing safe across syncs.
* :mod:`~pykantui.workspace.state` -- what the tracker said last time, which is
  what lets a sync tell your edits from theirs.
* :mod:`~pykantui.workspace.sync` -- push local edits, pull the tracker, write
  the files, commit.
"""

from __future__ import annotations

from pykantui.workspace.models import InvalidCard, PendingPush, SyncPlan, SyncReport
from pykantui.workspace.state import SyncState

#: Deliberately **not** re-exporting the ``sync`` function here. It would
#: shadow the :mod:`pykantui.workspace.sync` submodule of the same name, so
#: ``from pykantui.workspace import sync`` would hand you the function while
#: looking exactly like a module import. Import it from its own module.
__all__ = ["InvalidCard", "PendingPush", "SyncPlan", "SyncReport", "SyncState"]
