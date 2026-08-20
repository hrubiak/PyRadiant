"""Pack the minimal files needed to run run_pyradiant.py into a folder and zip it."""
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_NAME = "PyRadiant_pack"
OUT_DIR = ROOT / OUT_NAME
ZIP_PATH = ROOT / f"{OUT_NAME}.zip"

TOP_LEVEL_FILES = [
    "run_pyradiant.py",
    "run_pyradiant.bat",
    "run_pyradiant.sh",
    "requirements.txt",
    "license.txt",
    "README.md",
]

EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
EXCLUDE_FILES = {".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_skip(path: Path) -> bool:
    if path.name in EXCLUDE_FILES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    return any(part in EXCLUDE_DIRS for part in path.parts)


def copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for item in src.rglob("*"):
        if should_skip(item.relative_to(src)):
            continue
        target = dst / item.relative_to(src)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count


def main() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    OUT_DIR.mkdir()

    total = 0
    for name in TOP_LEVEL_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, OUT_DIR / name)
            total += 1
        else:
            print(f"  (skipped, missing: {name})")

    pkg_src = ROOT / "pyradiant"
    pkg_dst = OUT_DIR / "pyradiant"
    total += copy_tree(pkg_src, pkg_dst)

    print(f"Copied {total} files to {OUT_DIR}")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in OUT_DIR.rglob("*"):
            if item.is_file():
                zf.write(item, item.relative_to(ROOT))

    size_mb = ZIP_PATH.stat().st_size / (1024 * 1024)
    print(f"Wrote {ZIP_PATH} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
