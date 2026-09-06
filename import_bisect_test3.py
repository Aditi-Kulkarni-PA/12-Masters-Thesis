"""
Throwaway diagnostic #3: dumps a real stack trace of exactly where `import seaborn`
is frozen, using the stdlib faulthandler module (no special OS permissions needed,
unlike py-spy). Run from repo root:

    .venv/bin/python3 import_bisect_test3.py

If it hangs, wait the full 20 seconds -- faulthandler will then print the live stack
trace of every thread to stderr and exit on its own. Delete this file once done.
"""
import faulthandler
import sys

# If the whole process is still alive 20s from now, dump every thread's current
# stack trace to stderr and exit -- this shows the exact line seaborn's import
# chain is frozen on.
faulthandler.dump_traceback_later(20, exit=True, file=sys.stderr)

print("start", flush=True)
import seaborn as sns
print("seaborn ok -- dump_traceback_later did not fire", flush=True)
