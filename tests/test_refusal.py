"""The refusal path: an unrecognised binary must produce no output at all.

This is the single most important behavioural guarantee in the project.  The
patcher runs against /opt/resolve/bin/resolve on a machine where Resolve is
someone's working install; a new Resolve release that moves a patch site must
make the tool stop, not make it write something plausible-looking.  "Refuses
cleanly and writes nothing" is what the README promises, so it gets a test.

Run: python -m pytest tests/   (PYTHONPATH=.:vendor/pylibs)
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOT_RESOLVE = shutil.which("bash") or "/bin/bash"


def _run(args, tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [ROOT, os.path.join(ROOT, "vendor", "pylibs")])
    # Point at a trampoline that does not exist: if the tool ever got as far as
    # invoking e9tool we would rather see that than a mystery success.
    env["AAC_TRAMPOLINE"] = str(tmp_path / "no-such-trampoline")
    return subprocess.run([sys.executable, "-m", "aacpatch.additive"] + args,
                          capture_output=True, text=True, env=env, cwd=ROOT)


def test_unknown_binary_is_refused(tmp_path):
    out = tmp_path / "out.bin"
    r = _run([NOT_RESOLVE, "-o", str(out)], tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cannot locate a patch site" in r.stdout
    assert "no output written" in r.stdout
    assert not out.exists(), "a refused patch must leave no output file"


def test_missing_input_is_refused(tmp_path):
    out = tmp_path / "out.bin"
    r = _run([str(tmp_path / "nope"), "-o", str(out)], tmp_path)
    assert r.returncode == 1
    assert not out.exists()


def test_locate_reports_failure_without_writing(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [ROOT, os.path.join(ROOT, "vendor", "pylibs")])
    r = subprocess.run([sys.executable, "-m", "aacpatch.locate", NOT_RESOLVE],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 1
    assert "refusing to patch" in r.stdout
    # Both signature groups must be reported, not just the QuickTime one:
    # "do the MKV sites still resolve?" is half the diagnosis on a new build.
    assert "QuickTime" in r.stdout and "Matroska" in r.stdout
