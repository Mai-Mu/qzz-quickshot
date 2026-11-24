"""Build and commit a matching Windows application and source release."""

from contextlib import contextmanager
from hashlib import file_digest
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from collect_notices import collect_notices


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "qzz-quickshot"
PRODUCT_NAME = "瞬截"
SOURCE_VERSION = Path("quickshot/__init__.py")
VERSION_LINE = re.compile(rb'^__version__ = "((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"\r?$', re.M)
SOURCE_FILES = (
    "run_quickshot.py",
    "README.md",
    "AGENTS.md",
    "requirements.txt",
    "requirements-build.txt",
    "THIRD_PARTY_NOTICES.md",
    ".gitignore",
)


def version_match(source: bytes) -> re.Match[bytes]:
    matches = tuple(VERSION_LINE.finditer(source))
    if len(matches) != 1:
        raise ValueError(f'{SOURCE_VERSION} must contain one __version__ = "X.Y.Z" assignment.')
    return matches[0]


def source_version(source: bytes) -> str:
    return version_match(source)[1].decode("ascii")


def next_version(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    if max(major, minor, patch + 1) > 65535:
        raise ValueError(f"Version {version} cannot advance within Windows version resource limits.")
    return f"{major}.{minor}.{patch + 1}"


def archive_names(version: str) -> tuple[str, str]:
    return (
        f"{APP_NAME}-v{version}-windows-x64.zip",
        f"{APP_NAME}-v{version}-source.zip",
    )


@contextmanager
def build_lock(path: Path):
    with path.open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another Windows release build is active in this workspace.") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def stage_source(destination: Path, source: bytes, version: str) -> None:
    destination.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.py[cod]")
    for directory in ("quickshot", "scripts"):
        shutil.copytree(PROJECT_ROOT / directory, destination / directory, ignore=ignore)
    for name in SOURCE_FILES:
        shutil.copyfile(PROJECT_ROOT / name, destination / name)

    match = version_match(source)
    stamped = source[:match.start(1)] + version.encode("ascii") + source[match.end(1):]
    (destination / SOURCE_VERSION).write_bytes(stamped)


def write_version_resource(path: Path, version: str) -> None:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    fixed = (*map(int, version.split(".")), 0)
    strings = {
        "FileDescription": "瞬截截图工具",
        "FileVersion": version,
        "InternalName": APP_NAME,
        "OriginalFilename": f"{PRODUCT_NAME}.exe",
        "ProductName": PRODUCT_NAME,
        "ProductVersion": version,
    }
    resource = VSVersionInfo(
        FixedFileInfo(filevers=fixed, prodvers=fixed),
        [
            StringFileInfo([StringTable("080404B0", [StringStruct(key, value) for key, value in strings.items()])]),
            VarFileInfo([VarStruct("Translation", [0x0804, 1200])]),
        ],
    )
    path.write_text(str(resource), encoding="utf-8")


def bundle_application(session: Path, source: Path, version: str) -> Path:
    system_root = Path(os.environ["SystemRoot"])
    system32 = system_root / "System32"
    powershell = system32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PROJECT_ROOT / "scripts/prepare_ocr.ps1"],
        cwd=PROJECT_ROOT,
        check=True,
    )

    version_resource = session / "windows-version.txt"
    write_version_resource(version_resource, version)
    bundle_root = session / "bundle"
    icon = source / "quickshot/assets/quickshot.ico"
    runtime = PROJECT_ROOT / "ocr_runtime"
    environment = os.environ.copy()
    # Use Windows ICU rather than incompatible libraries from other toolchains on PATH.
    environment["PATH"] = os.pathsep.join(map(str, (system32, system_root)))
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--onedir", "--windowed", "--noupx",
            "--name", APP_NAME, "--icon", icon, "--version-file", version_resource,
            "--specpath", session, "--workpath", session / "work", "--distpath", bundle_root,
            "--add-data", f"{runtime};ocr_runtime", "--add-data", f"{icon};quickshot/assets",
            source / "run_quickshot.py",
        ],
        cwd=source,
        env=environment,
        check=True,
    )

    bundle = bundle_root / APP_NAME
    for dll in ("icu.dll", "icuuc.dll", "icuin.dll"):
        if (bundle / "_internal" / dll).exists():
            raise RuntimeError(f"Unexpected app-local {dll}. The Windows release uses the system ICU.")
    (bundle / f"{APP_NAME}.exe").rename(bundle / f"{PRODUCT_NAME}.exe")
    shutil.copyfile(source / "README.md", bundle / "README.md")
    collect_notices(bundle)
    return bundle


def create_archive(directory: Path, destination: Path) -> None:
    with ZipFile(destination, "x", compression=ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory.parent))


def verify_package(bundle: Path, application_archive: Path, source_archive: Path, version: str) -> None:
    from PyInstaller.archive.readers import CArchiveReader
    from PyInstaller.utils.win32.versioninfo import StringFileInfo, read_version_info_from_executable

    executable = bundle / f"{PRODUCT_NAME}.exe"
    info = read_version_info_from_executable(str(executable))
    if info is None:
        raise RuntimeError("Windows version information is missing from the executable.")
    major, minor, patch = map(int, version.split("."))
    fixed = ((major << 16) | minor, patch << 16)
    strings = {
        string.name: string.val
        for section in info.kids if isinstance(section, StringFileInfo)
        for table in section.kids for string in table.kids
    }
    if (
        (info.ffi.fileVersionMS, info.ffi.fileVersionLS) != fixed
        or (info.ffi.productVersionMS, info.ffi.productVersionLS) != fixed
        or strings.get("FileVersion") != version
        or strings.get("ProductVersion") != version
    ):
        raise RuntimeError(f"Windows executable version does not match {version}.")

    frozen = CArchiveReader(str(executable)).open_embedded_archive("PYZ.pyz").extract("quickshot")
    if version not in getattr(frozen, "co_consts", ()):
        raise RuntimeError(f"Frozen application version does not match {version}.")

    with ZipFile(application_archive) as archive:
        if archive.testzip() or archive.read(f"{APP_NAME}/{executable.name}") != executable.read_bytes():
            raise RuntimeError("Windows archive verification failed.")
    with ZipFile(source_archive) as archive:
        if any("__pycache__" in Path(name).parts or Path(name).suffix in {".pyc", ".pyo"}
               for name in archive.namelist()):
            raise RuntimeError("The source archive contains generated Python files.")
        if archive.testzip() or source_version(archive.read(f"{APP_NAME}/{SOURCE_VERSION.as_posix()}")) != version:
            raise RuntimeError(f"Source archive version does not match {version}.")


def same_contents(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_file, right.open("rb") as right_file:
        return file_digest(left_file, "sha256").digest() == file_digest(right_file, "sha256").digest()


def ensure_new_archives(output: Path, version: str) -> None:
    for name in archive_names(version):
        target = output / name
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Release archive already exists: {target}")


def recover_publish(journal: Path, output: Path, version_file: Path) -> None:
    if not journal.exists():
        return
    pending = json.loads(journal.read_text(encoding="utf-8"))
    if not re.fullmatch(r"[0-9a-f]{32}", pending["session"]):
        raise RuntimeError("Release recovery found an invalid staging directory name.")
    session = journal.parent / pending["session"]
    expected = pending["version"]
    current = source_version(version_file.read_bytes())
    if expected != next_version(pending["previous"]) or current not in (pending["previous"], expected):
        raise RuntimeError("The source version changed while a release was being published.")

    for name in archive_names(expected):
        published = output / name
        valid_archive = published.is_file() and not published.is_symlink() and same_contents(session / name, published)
        if current == expected:
            if not valid_archive:
                raise RuntimeError(f"The committed release archive is missing or has changed: {published}")
        elif valid_archive:
            published.unlink()
    journal.unlink()


def publish(
    session: Path, journal: Path, output: Path, version_file: Path, original: bytes, stamped: bytes, version: str,
) -> None:
    ensure_new_archives(output, version)
    if version_file.read_bytes() != original:
        raise RuntimeError(f"{SOURCE_VERSION} changed during the build.")
    journal_draft = session / "pending-release.json"
    journal_draft.write_text(
        json.dumps({"previous": source_version(original), "version": version, "session": session.name}),
        encoding="utf-8",
    )
    journal_draft.replace(journal)
    try:
        for name in archive_names(version):
            copy = session / f"{name}.publish"
            shutil.copyfile(session / name, copy)
            # Windows rename preserves an existing destination if it was created after the preflight.
            copy.rename(output / name)
        updated_version = session / "published-version.py"
        updated_version.write_bytes(stamped)
        if version_file.read_bytes() != original:
            raise RuntimeError(f"{SOURCE_VERSION} changed during the build.")
        updated_version.replace(version_file)
    except BaseException:
        recover_publish(journal, output, version_file)
        raise
    else:
        try:
            journal.unlink()
        except OSError as error:
            print(f"Release committed. The next build can clear the recovery record: {error}", file=sys.stderr)


def build() -> None:
    if sys.version_info[:2] != (3, 12) or sysconfig.get_platform() != "win-amd64":
        raise RuntimeError("This release needs 64-bit Python 3.12 and requirements-build.txt.")

    build_root = PROJECT_ROOT / "build" / "windows-x64"
    output = PROJECT_ROOT / "dist"
    version_file = PROJECT_ROOT / SOURCE_VERSION
    build_root.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=True)
    with build_lock(build_root / ".build.lock"):
        journal = build_root / "pending-release.json"
        recover_publish(journal, output, version_file)
        original = version_file.read_bytes()
        version = next_version(source_version(original))
        ensure_new_archives(output, version)
        session = build_root / uuid4().hex
        session.mkdir()
        try:
            print(f"Building {APP_NAME} {version}", flush=True)
            source = session / "source" / APP_NAME
            stage_source(source, original, version)
            application_archive, source_archive = (session / name for name in archive_names(version))
            create_archive(source, source_archive)
            bundle = bundle_application(session, source, version)
            create_archive(bundle, application_archive)
            verify_package(bundle, application_archive, source_archive, version)
            publish(session, journal, output, version_file, original, (source / SOURCE_VERSION).read_bytes(), version)
        finally:
            # Preserve the recovery data if publishing was interrupted.
            if not journal.exists():
                resolved = session.resolve()
                if resolved.parent == build_root.resolve() and resolved.is_relative_to(PROJECT_ROOT.resolve()):
                    shutil.rmtree(resolved, ignore_errors=True)

        for name in archive_names(version):
            print(f"Release ready at {output / name}")


if __name__ == "__main__":
    try:
        build()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Build failed: {error}")
