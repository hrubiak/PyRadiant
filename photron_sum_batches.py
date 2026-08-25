"""Sum consecutive Photron TIFF frames in batches of N.

Configure the folder + batch size below, then run:
    python photron_sum_batches.py

Reads all .tif/.tiff files in FOLDER, sorts them naturally, sums
consecutive batches of BATCH frames pixel-wise, and writes each sum as
one TIFF into FOLDER/summed_Nx/. If the file count is M*N+K with K > 0,
the trailing K frames are summed into one final (partial) output.

Output filenames encode the source frame range, e.g. sum_00000-00009.tif
for the first batch of N=10.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile
from natsort import natsorted


# ---------------------------------------------------------------------
# CONFIG — edit these
# ---------------------------------------------------------------------
FOLDER = Path('/Users/hrubiak/Downloads/20260824-photron/20260824/calib1/C001H001S0001')  # <-- set this
BATCH = 60                                    # frames per output
DTYPE = 'uint32'                              # 'uint32' or 'float32'
OUT_FOLDER: Path | None = None                # None → FOLDER/summed_Nx
# ---------------------------------------------------------------------


TIF_EXTS = ('.tif', '.tiff', '.TIF', '.TIFF')


def find_tifs(folder: Path) -> list[Path]:
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix in TIF_EXTS]
    return [Path(p) for p in natsorted(files, key=lambda p: p.name)]


def sum_batch(paths: list[Path], out_dtype: np.dtype) -> np.ndarray:
    """Load `paths` and return their pixel-wise sum as `out_dtype`.

    Reads the first frame to size the accumulator, then adds the rest
    in-place. Fails loudly if a later frame has a different shape."""
    first = tifffile.imread(str(paths[0]))
    acc = first.astype(out_dtype, copy=True)
    for p in paths[1:]:
        arr = tifffile.imread(str(p))
        if arr.shape != first.shape:
            raise ValueError(
                f'shape mismatch: {p.name} is {arr.shape}, '
                f'first frame {paths[0].name} is {first.shape}')
        acc += arr.astype(out_dtype, copy=False)
    return acc


def batch_ranges(n_files: int, batch: int) -> list[tuple[int, int]]:
    """Return [(start, end_exclusive), ...] covering all files. Trailing
    partial batch (K < batch) is included as the final range."""
    ranges = []
    for start in range(0, n_files, batch):
        end = min(start + batch, n_files)
        ranges.append((start, end))
    return ranges


def run(folder: Path, batch: int, dtype: str,
        out_folder: Path | None = None) -> None:
    if not folder.is_dir():
        raise SystemExit(f'error: {folder} is not a directory')
    if batch < 1:
        raise SystemExit('error: BATCH must be >= 1')

    files = find_tifs(folder)
    if not files:
        raise SystemExit(f'error: no .tif/.tiff files in {folder}')

    out_folder = out_folder or (folder / f'summed_{batch}x')
    out_folder.mkdir(parents=True, exist_ok=True)

    out_dtype = np.dtype(dtype)
    ranges = batch_ranges(len(files), batch)
    n_full = sum(1 for s, e in ranges if e - s == batch)
    n_partial = len(ranges) - n_full
    print(f'input : {folder}')
    print(f'found : {len(files)} frames')
    print(f'batch : {batch} → {n_full} full + {n_partial} partial '
          f'= {len(ranges)} outputs')
    print(f'dtype : {out_dtype}')
    print(f'output: {out_folder}')

    width = max(5, len(str(len(files))))
    for i, (start, end) in enumerate(ranges):
        batch_paths = files[start:end]
        acc = sum_batch(batch_paths, out_dtype)
        name = (f'sum_{start:0{width}d}-{end - 1:0{width}d}'
                f'{batch_paths[0].suffix.lower()}')
        out_path = out_folder / name
        tifffile.imwrite(str(out_path), acc)
        tag = '' if end - start == batch else f'  (partial, {end-start} frames)'
        print(f'  [{i+1}/{len(ranges)}] {name}{tag}')


if __name__ == '__main__':
    run(FOLDER, BATCH, DTYPE, OUT_FOLDER)
