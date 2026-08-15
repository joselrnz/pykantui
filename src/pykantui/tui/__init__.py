"""The terminal UI: the app, its themes, and the widgets on the board.

Deliberately import-free at package level. The app imports the pages it pushes,
and a page reaches back into :mod:`pykantui.tui.widgets` for its fields, so
importing the app from here would make ``import pykantui.tui.widgets.card``
pull in a half-built app. Import :class:`~pykantui.tui.app.KanbanApp` from its
own module.
"""
