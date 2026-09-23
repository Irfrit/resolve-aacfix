import hashlib
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def make_package(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    for name in ("install.sh", "uninstall.sh", "aac-fix", "aac-patch-tree", "aac-fix-reapply"):
        shutil.copy2(ROOT / name, package / name)
    for name in ("aacpatch", "systemd", "tmpfiles.d", "polkit"):
        shutil.copytree(ROOT / name, package / name)
    (package / "vendor").mkdir()
    (package / "prebuilt").mkdir()
    digest = hashlib.sha256((package / "aac-fix").read_bytes()).hexdigest()
    (package / "SHA256SUMS").write_text(f"{digest}  aac-fix\n")
    return package


def test_install_and_uninstall_layout(tmp_path):
    package = make_package(tmp_path)
    target = tmp_path / "root"
    result = subprocess.run(
        [str(package / "install.sh"), "--root", str(target), "--no-auto-reapply", "--no-systemd"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    libdir = target / "usr/lib/resolve-aacfix"
    assert (libdir / "aac-fix").is_file()
    assert (libdir / "aac-fix-reapply").is_file()
    assert (target / "usr/bin/aac-fix").is_symlink()
    assert os.readlink(target / "usr/bin/aac-fix") == "/usr/lib/resolve-aacfix/aac-fix"
    assert (target / "usr/lib/systemd/system/resolve-aacfix-reapply.service").is_file()
    assert (target / "usr/lib/systemd/system/resolve-aacfix-reapply.path").is_file()
    assert (target / "usr/lib/tmpfiles.d/resolve-aacfix.conf").is_file()
    assert (target / "usr/share/polkit-1/actions/50-resolve-aacfix.policy").is_file()
    if os.geteuid() == 0:
        assert (libdir / "aac-fix").stat().st_uid == 0
        assert (libdir / "aac-fix").stat().st_mode & 0o022 == 0
    assert (target / "etc/resolve-aacfix/auto.conf").read_text() == "mode=disabled\n"

    result = subprocess.run(
        [str(package / "uninstall.sh"), "--root", str(target), "--no-revert"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (target / "usr/bin/aac-fix").exists()
    assert not libdir.exists()
    assert not (target / "usr/lib/systemd/system/resolve-aacfix-reapply.service").exists()
