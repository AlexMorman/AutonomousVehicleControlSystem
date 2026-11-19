""" 
This script is used to take the images saved to /recordings/ and create a .gif file from those images.
We only send the .jpgs to the server for classification, but for visualization purposes it is still useful to recreate video based on the real frame rate


usage:

    video_recreation.py path/to/frames -o path/to/output/filename.gif
    video_recreation.py frames --hold 1.0
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
 
PATTERN = re.compile(r"_(\d\d)-(\d\d)-(\d\d(?:\.\d+)?)\.jpe?g$", re.IGNORECASE)
 
 
def timestamp(path: Path) -> float:
    m = PATTERN.search(path.name)
    if not m:
        raise ValueError(f"Can't read timestamp from: {path.name}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)
 
 
def make_list(folder: Path, list_path: Path, hold: float) -> int:
    """Write an ffmpeg concat list with per-frame durations. Returns frame count."""
    files = sorted(p for p in folder.glob("frame_*.jp*g") if PATTERN.search(p.name))
    if not files:
        sys.exit(f"No matching frame_*.jpg files found in {folder}")
 
    def entry(p: Path) -> str:
        # Absolute path, with single quotes escaped for the concat format
        return "file '" + str(p.resolve()).replace("'", r"'\''") + "'\n"
 
    with open(list_path, "w") as out:
        for a, b in zip(files, files[1:]):
            d = timestamp(b) - timestamp(a)
            if d < 0:          # capture crossed midnight
                d += 86400
            out.write(entry(a) + f"duration {d:.3f}\n")
        # Last frame: give it a hold time, then repeat it (concat ignores the final duration otherwise)
        out.write(entry(files[-1]) + f"duration {hold:.3f}\n" + entry(files[-1]))
    return len(files)
 
 
def run_ffmpeg(list_path: Path, output: Path) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "warning", "-stats",
           "-f", "concat", "-safe", "0", "-i", str(list_path),
           "-vf", "split[a][b];[a]palettegen[p];[b][p]paletteuse",
           str(output)]
    subprocess.run(cmd, check=True)
 
 
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=".", help="folder with the frames (default: current)")
    ap.add_argument("-o", "--output", default="out.gif", help="output GIF file (default: out.gif)")
    ap.add_argument("--hold", type=float, default=0.2, help="seconds to show the last frame (default: 0.2)")
    ap.add_argument("--keep-list", action="store_true", help="save list.txt next to the output for inspection")
    args = ap.parse_args()
 
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found on PATH")
 
    folder = Path(args.folder)
    output = Path(args.output)
    if output.suffix.lower() != ".gif":
        output = output.with_suffix(".gif")
 
    with tempfile.TemporaryDirectory() as tmp:
        list_path = Path(tmp) / "list.txt"
        n = make_list(folder, list_path, args.hold)
        print(f"Found {n} frames")
        if args.keep_list:
            shutil.copy(list_path, output.with_name("list.txt"))
        try:
            run_ffmpeg(list_path, output)
        except subprocess.CalledProcessError:
            sys.exit("ffmpeg failed")
 
    print(f"Wrote {output}")
 
 
if __name__ == "__main__":
    main()
