"""Temporary gateway boot-capture wrapper (debug). Redirects stdout+stderr to
the HERMES_HOME volume so a crash-loop traceback is recoverable, then runs the
dashboard exactly as the CMD would."""
import os, runpy, sys

home = os.environ.get("HERMES_HOME", "/data/hermes")
os.makedirs(home, exist_ok=True)
logf = open(os.path.join(home, "full-boot.log"), "w", buffering=1)
sys.stdout = sys.stderr = logf

sys.argv = ["hermes", "dashboard", "--no-open", "--insecure", "--skip-build",
            "--host", "0.0.0.0", "--port", "9320"]
try:
    runpy.run_module("hermes_cli.main", run_name="__main__")
except BaseException:
    import traceback
    traceback.print_exc()
    logf.flush()
    raise
