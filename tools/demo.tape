# Records the README demo. Requires vhs, ttyd and ffmpeg on PATH:
#
#     winget install charmbracelet.vhs tsl0922.ttyd Gyan.FFmpeg
#     vhs tools/demo.tape
#
# It drives the real app against `kbn demo` — a throwaway in-memory board, so
# recording this never touches a saved board.

Output assets/demo.gif

Set Shell powershell
Set FontSize 15
Set Width 1500
Set Height 820
Set Padding 24
Set Framerate 24
Set TypingSpeed 90ms

# Start with a clean screen. vhs inherits the directory it was invoked from,
# so run this from the repo root and no cd is needed — an absolute path here
# would only work on the machine that wrote it.
Hide
Type "clear"
Enter
Sleep 1s
Show

Type ".\.venv\Scripts\kbn.exe demo"
Sleep 500ms
Enter
Sleep 3s

# Move the cursor across columns — lowercase moves focus.
Type "l"
Sleep 700ms
Type "l"
Sleep 900ms
Type "h"
Sleep 900ms

# Uppercase moves the card. Confirmation is on by default, so this shows the
# dialog naming both columns and the side effect.
Type "L"
Sleep 2s
Enter
Sleep 1500ms

# Open a card: dates, dependencies, description, Jira fields.
Type "v"
Sleep 2500ms
Escape
Sleep 1s

# Collapse a column to a strip, then expand everything again.
Type "z"
Sleep 1800ms
Type "Z"
Sleep 1200ms

# Search filters live, and the count reads "n of m" while it does.
Type "/"
Sleep 800ms
Type "ship"
Sleep 2500ms
Backspace 4
Sleep 1500ms

Escape
Sleep 500ms
Ctrl+Q
Sleep 1500ms
