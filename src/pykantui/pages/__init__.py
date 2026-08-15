"""Full-screen views pushed over the board: card detail, edit, menus, confirms.

Deliberately import-free at package level. A page may reach into
:mod:`pykantui.tui.widgets` for a field or a label, and a widget may push a
page, so eagerly importing every page here would tangle the two.
"""
