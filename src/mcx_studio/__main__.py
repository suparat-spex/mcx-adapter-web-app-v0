"""python -m mcx_studio  (also works when this file is run directly, e.g. the IDE's Run button)."""
import sys

if __package__ in (None, ""):                     # run as a script: put src/ on the path
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcx_studio.app import main  # noqa: E402

sys.exit(main())
