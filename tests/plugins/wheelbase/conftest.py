"""Make the wheelbase plugin packages importable in tests.

plugins/wheelbase/ is not on sys.path by default because it's a subdirectory
of the bundled plugins tree rather than a top-level Python package. Insert it
so `import wheelbase_core` etc. resolve to the in-repo source without any
install step. wheelbase_sdk is pip-installed (Task A1) so it resolves normally.

The root conftest adds PROJECT_ROOT to sys.path so that hermes_* packages are
importable. However, PROJECT_ROOT also contains a wheelbase_sdk/ *directory*
(the sdist root, not the inner package). Python's PathFinder, which sits
before the editable-install _EditableFinder in sys.meta_path, would match that
directory as a namespace package and shadow the real package. We promote the
editable finder to run first so wheelbase_sdk always resolves to the installed
package.
"""
import sys
from pathlib import Path

# Promote the wheelbase_sdk editable-install finder so it runs before
# PathFinder and isn't shadowed by the wheelbase_sdk/ directory in PROJECT_ROOT.
# Editable finders are classes (type objects) appended to sys.meta_path.
_finders_to_promote = [
    f for f in sys.meta_path
    if (isinstance(f, type) and f.__name__ == "_EditableFinder"
        and getattr(f, "__module__", "").startswith("__editable___wheelbase_sdk"))
]
for _f in _finders_to_promote:
    sys.meta_path.remove(_f)
    sys.meta_path.insert(0, _f)

# PROJECT_ROOT/plugins/wheelbase/ holds the four wheelbase plugin packages.
_PLUGINS_WB_DIR = Path(__file__).resolve().parents[3] / "plugins" / "wheelbase"
if str(_PLUGINS_WB_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_WB_DIR))
