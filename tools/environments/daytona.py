"""Daytona cloud execution environment.

Uses the Daytona Python SDK to run commands in cloud sandboxes.
Supports persistent sandboxes: when enabled, sandboxes are stopped on cleanup
and resumed on next creation, preserving the filesystem across sessions.
"""

import logging
import math
import os
import shlex
import threading
import uuid
from pathlib import Path

from tools.environments.base import (
    BaseEnvironment,
    _ThreadedProcessHandle,
)
from tools.environments.file_sync import (
    FileSyncManager,
    iter_sync_files,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)

logger = logging.getLogger(__name__)


# Kill a command's whole process tree from inside the sandbox WITHOUT procps.
# The slim Daytona image has no pkill/pgrep/ps — only the bash `kill` builtin —
# so we read the wrapped shell's PID from a per-command file, build the
# parent→children map from /proc/<pid>/status, and SIGKILL the root plus every
# descendant. python3 is present in the image; its own process is a sibling of
# the target (not a descendant), so it can never kill itself. ``__PIDFILE__`` is
# substituted with a safe /tmp/HERMESKILL_<hex>.pid path (no shell metachars).
_DAYTONA_KILL_TEMPLATE = """import os,signal,glob
try:
 root=int(open('__PIDFILE__').read().strip())
except Exception:
 raise SystemExit
kids={}
for s in glob.glob('/proc/[0-9]*/status'):
 try:
  pid=int(s.split('/')[2]); ppid=0
  for ln in open(s):
   if ln.startswith('PPid:'): ppid=int(ln.split()[1]); break
  kids.setdefault(ppid,[]).append(pid)
 except Exception: pass
seen=[]
stack=[root]
while stack:
 p=stack.pop()
 if p in seen: continue
 seen.append(p)
 stack+=kids.get(p,[])
for p in seen:
 try: os.kill(p,signal.SIGKILL)
 except Exception: pass
"""


class DaytonaEnvironment(BaseEnvironment):
    """Daytona cloud sandbox execution backend.

    Spawn-per-call via _ThreadedProcessHandle wrapping blocking SDK calls.
    cancel_fn wired to sandbox.stop() for interrupt support.
    Shell timeout wrapper preserved (SDK timeout unreliable).
    """

    _stdin_mode = "heredoc"

    def __init__(
        self,
        image: str,
        cwd: str = "/home/daytona",
        timeout: int = 60,
        cpu: int = 1,
        memory: int = 5120,
        disk: int = 10240,
        persistent_filesystem: bool = True,
        task_id: str = "default",
        always_on: bool = False,
    ):
        requested_cwd = cwd
        super().__init__(cwd=cwd, timeout=timeout)
        # Honoured by the terminal-tool idle reaper: an always-on sandbox is
        # kept warm (never stopped on idle) for instant next-turn response.
        self._always_on = always_on

        # Hard wall-clock cap for blocking Daytona control-plane calls
        # (get/create/start/stop/refresh_data). These otherwise have NO timeout,
        # so a slow/unreachable Daytona control plane hangs the terminal tool
        # forever — before the command-level timeout in _wait_for_process can
        # ever apply. See _call_with_timeout.
        try:
            self._lifecycle_timeout = max(
                5, int(os.environ.get("TERMINAL_DAYTONA_LIFECYCLE_TIMEOUT", "90"))
            )
        except ValueError:
            self._lifecycle_timeout = 90

        try:
            from tools.lazy_deps import ensure as _lazy_ensure
            _lazy_ensure("terminal.daytona", prompt=False)
        except ImportError:
            pass
        except Exception as e:
            raise ImportError(str(e))
        from daytona import (
            Daytona,
            CreateSandboxFromImageParams,
            DaytonaError,
            Resources,
            SandboxState,
        )

        self._persistent = persistent_filesystem
        self._task_id = task_id
        self._SandboxState = SandboxState
        self._daytona = Daytona()
        self._sandbox = None
        self._lock = threading.Lock()

        memory_gib = max(1, math.ceil(memory / 1024))
        disk_gib = max(1, math.ceil(disk / 1024))
        if disk_gib > 10:
            logger.warning(
                "Daytona: requested disk (%dGB) exceeds platform limit (10GB). "
                "Capping to 10GB.", disk_gib,
            )
            disk_gib = 10
        resources = Resources(cpu=cpu, memory=memory_gib, disk=disk_gib)

        labels = {"hermes_task_id": task_id}
        sandbox_name = f"hermes-{task_id}"

        if self._persistent:
            try:
                self._sandbox = self._call_with_timeout(
                    "get", lambda: self._daytona.get(sandbox_name)
                )
                self._call_with_timeout("start", self._sandbox.start)
                logger.info("Daytona: resumed sandbox %s for task %s",
                            self._sandbox.id, task_id)
            except DaytonaError:
                self._sandbox = None
            except Exception as e:
                logger.warning("Daytona: failed to resume sandbox for task %s: %s",
                               task_id, e)
                self._sandbox = None

            if self._sandbox is None:
                try:
                    # Daytona SDK >=0.108.0 uses cursor-based pagination and
                    # list() returns an iterator. Offset-based pagination
                    # (page=1) is removed on June 10, 2026.
                    results = self._call_with_timeout(
                        "list", lambda: self._daytona.list(labels=labels, limit=1)
                    )
                    legacy = next(iter(results), None)
                    if legacy is not None:
                        self._sandbox = legacy
                        self._call_with_timeout("start", self._sandbox.start)
                        logger.info("Daytona: resumed legacy sandbox %s for task %s",
                                    self._sandbox.id, task_id)
                except Exception as e:
                    logger.debug("Daytona: no legacy sandbox found for task %s: %s",
                                 task_id, e)
                    self._sandbox = None

        if self._sandbox is None:
            self._sandbox = self._call_with_timeout(
                "create",
                lambda: self._daytona.create(
                    CreateSandboxFromImageParams(
                        image=image,
                        name=sandbox_name,
                        labels=labels,
                        auto_stop_interval=0,
                        resources=resources,
                    )
                ),
            )
            logger.info("Daytona: created sandbox %s for task %s",
                        self._sandbox.id, task_id)

        # Detect remote home dir
        self._remote_home = "/root"
        try:
            home = self._sandbox.process.exec("echo $HOME").result.strip()
            if home:
                self._remote_home = home
                if requested_cwd in {"~", "/home/daytona"}:
                    self.cwd = home
        except Exception:
            pass
        logger.info("Daytona: resolved home to %s, cwd to %s", self._remote_home, self.cwd)

        # Ensure the working directory exists. In docker mode /workspace is a
        # bind-mounted volume that always exists; a Daytona sandbox has no such
        # mount, so a requested cwd (e.g. the cloud gateway's /workspace) must be
        # created or every command would fail with "no such file or directory".
        # The sandbox is persistent, so this dir survives stop/start like the
        # rest of the filesystem.
        try:
            self._sandbox.process.exec(f"mkdir -p {shlex.quote(self.cwd)}")
        except Exception as e:
            logger.warning("Daytona: could not create cwd %s: %s", self.cwd, e)

        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.hermes"),
            upload_fn=self._daytona_upload,
            delete_fn=self._daytona_delete,
            bulk_upload_fn=self._daytona_bulk_upload,
            bulk_download_fn=self._daytona_bulk_download,
        )
        self._sync_manager.sync(force=True)
        self.init_session()

    def ensure_cwd(self) -> None:
        """Create the current cwd inside the persistent Daytona sandbox."""
        try:
            self._sandbox.process.exec(f"mkdir -p {shlex.quote(self.cwd)}")
        except Exception as e:
            logger.warning("Daytona: could not create cwd %s: %s", self.cwd, e)

    def _daytona_upload(self, host_path: str, remote_path: str) -> None:
        """Upload a single file via Daytona SDK."""
        parent = str(Path(remote_path).parent)
        self._sandbox.process.exec(quoted_mkdir_command([parent]))
        self._sandbox.fs.upload_file(host_path, remote_path)

    def _daytona_bulk_upload(self, files: list[tuple[str, str]]) -> None:
        """Upload many files in a single HTTP call via Daytona SDK.

        Uses ``sandbox.fs.upload_files()`` which batches all files into one
        multipart POST, avoiding per-file TLS/HTTP overhead (~580 files
        goes from ~5 min to <2 s).
        """
        from daytona.common.filesystem import FileUpload

        if not files:
            return

        parents = unique_parent_dirs(files)
        if parents:
            self._sandbox.process.exec(quoted_mkdir_command(parents))

        uploads = [
            FileUpload(source=host_path, destination=remote_path)
            for host_path, remote_path in files
        ]
        self._sandbox.fs.upload_files(uploads)

    def _daytona_bulk_download(self, dest: Path) -> None:
        """Download remote .hermes/ as a tar archive."""
        rel_base = f"{self._remote_home}/.hermes".lstrip("/")
        # PID-suffixed remote temp path avoids collisions if sync_back fires
        # concurrently for the same sandbox (e.g. retry after partial failure).
        remote_tar = f"/tmp/.hermes_sync.{os.getpid()}.tar"
        self._sandbox.process.exec(
            f"tar cf {shlex.quote(remote_tar)} -C / {shlex.quote(rel_base)}"
        )
        self._sandbox.fs.download_file(remote_tar, str(dest))
        # Clean up remote temp file
        try:
            self._sandbox.process.exec(f"rm -f {shlex.quote(remote_tar)}")
        except Exception:
            pass  # best-effort cleanup

    def _daytona_delete(self, remote_paths: list[str]) -> None:
        """Batch-delete remote files via SDK exec."""
        self._sandbox.process.exec(quoted_rm_command(remote_paths))

    # ------------------------------------------------------------------
    # Sandbox lifecycle
    # ------------------------------------------------------------------

    def _call_with_timeout(self, label: str, fn):
        """Run a blocking Daytona control-plane SDK call with a hard wall-clock cap.

        The Daytona SDK's own timeouts are unreliable (see class docstring) and
        the lifecycle calls (get/create/start/stop/refresh_data) otherwise have
        none — a slow or unreachable Daytona control plane would hang the
        terminal tool indefinitely, before the command-level timeout in
        _wait_for_process can apply. Runs ``fn`` on a daemon thread (so a truly
        stuck SDK call can't block interpreter shutdown) and raises TimeoutError
        if it overruns, letting the caller surface an error instead of blocking.
        """
        box: dict = {}

        def runner() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
                box["error"] = exc

        worker = threading.Thread(
            target=runner, name=f"daytona-{label}", daemon=True
        )
        worker.start()
        worker.join(self._lifecycle_timeout)
        if worker.is_alive():
            raise TimeoutError(
                f"Daytona {label} did not complete within "
                f"{self._lifecycle_timeout}s (control plane slow or unreachable)"
            )
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _ensure_sandbox_ready(self) -> None:
        """Restart sandbox if it was stopped (e.g., by a previous interrupt)."""
        self._call_with_timeout("refresh_data", self._sandbox.refresh_data)
        if self._sandbox.state in {self._SandboxState.STOPPED, self._SandboxState.ARCHIVED}:
            self._call_with_timeout("start", self._sandbox.start)
            logger.info("Daytona: restarted sandbox %s", self._sandbox.id)

    def _before_execute(self) -> None:
        """Ensure sandbox is ready, then sync files via FileSyncManager."""
        with self._lock:
            self._ensure_sandbox_ready()
        self._sync_manager.sync()

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None):
        """Return a _ThreadedProcessHandle wrapping a blocking Daytona SDK call."""
        sandbox = self._sandbox
        lock = self._lock

        # Per-command PID file: the wrapped shell records its own PID here so
        # cancel() can kill exactly this command's process tree (and nothing
        # else) on interrupt/timeout. Unique name avoids collisions across
        # concurrent commands in the same sandbox.
        kill_tag = "HERMESKILL_" + uuid.uuid4().hex
        pidfile = f"/tmp/{kill_tag}.pid"

        def cancel():
            # Always-on (warm) sandboxes must NOT be stopped on interrupt/timeout:
            # stopping cold-restarts them next turn (observed as stop/start churn
            # in prod) and defeats the point of keeping them warm. Kill only this
            # command's process tree via the recorded PID, leaving the sandbox up.
            # The slim image has no procps, so the kill is done by python3 walking
            # /proc (see _DAYTONA_KILL_TEMPLATE).
            killer = _DAYTONA_KILL_TEMPLATE.replace("__PIDFILE__", pidfile)
            try:
                self._call_with_timeout(
                    "kill",
                    lambda: sandbox.process.exec(
                        f"python3 -c {shlex.quote(killer)}; "
                        f"rm -f {shlex.quote(pidfile)} 2>/dev/null || true"
                    ),
                )
            except Exception:
                pass
            # Ephemeral (non-always-on) sandboxes are still stopped as before so
            # they are reclaimed promptly.
            if not self._always_on:
                with lock:
                    try:
                        self._call_with_timeout("stop", sandbox.stop)
                    except Exception:
                        pass

        # Record the wrapped shell's PID before running the command so cancel()
        # can find its process tree. `echo $$` writes the `bash -c` PID; it
        # produces no stdout (redirected) so it cannot disturb CWD-marker parsing.
        cmd_with_pid = f"echo $$ > {shlex.quote(pidfile)}\n{cmd_string}"
        if login:
            shell_cmd = f"bash -l -c {shlex.quote(cmd_with_pid)}"
        else:
            shell_cmd = f"bash -c {shlex.quote(cmd_with_pid)}"

        def exec_fn() -> tuple[str, int]:
            response = sandbox.process.exec(shell_cmd, timeout=timeout)
            return (response.result or "", response.exit_code)

        return _ThreadedProcessHandle(exec_fn, cancel_fn=cancel)

    def cleanup(self):
        with self._lock:
            if self._sandbox is None:
                return

            # Sync remote changes back to host before teardown. Running
            # inside the lock (and after the _sandbox is None guard) avoids
            # firing sync_back on an already-cleaned-up env, which would
            # trigger a 3-attempt retry storm against a nil sandbox.
            if self._sync_manager:
                logger.info("Daytona: syncing files from sandbox...")
                try:
                    self._sync_manager.sync_back()
                except Exception as e:
                    logger.warning("Daytona: sync_back failed: %s", e)

            try:
                if self._persistent:
                    self._sandbox.stop()
                    logger.info("Daytona: stopped sandbox %s (filesystem preserved)",
                                self._sandbox.id)
                else:
                    self._daytona.delete(self._sandbox)
                    logger.info("Daytona: deleted sandbox %s", self._sandbox.id)
            except Exception as e:
                logger.warning("Daytona: cleanup failed: %s", e)
            self._sandbox = None
