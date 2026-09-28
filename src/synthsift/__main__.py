"""``python -m synthsift`` – and ``python src/synthsift`` / ``uv run src/synthsift`` (the folder run as a script)."""

import sys

if __package__:
    from .cli import main
else:  # run by path: there is no parent package, so make ``synthsift`` importable from its folder
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from synthsift.cli import main

if __name__ == "__main__":  # worker processes import this module too
    try:
        main()
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] == "synthsift":
            raise
        root = Path(__file__).resolve().parents[2] if not __package__ else "<SynthSift folder>"
        sys.exit(f"{exc}: this Python doesn't have SynthSift's dependencies.\n"
                 f"Run it through its project instead:  uv run --project {root} synthsift serve --load <zip>")
