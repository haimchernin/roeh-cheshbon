#!/bin/zsh
# לחיצה כפולה: מפעיל את המסך ופותח אותו בדפדפן. סוגרים את החלון = המסך נסגר.
cd "$(dirname "$0")"
if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then open "http://localhost:8765"; exit 0; fi
( sleep 1; open "http://localhost:8765" ) &
python3 serve.py
