import sys

print("AutoDeck2 starting...", flush=True)

from .cli import main  # noqa: E402

sys.exit(main())
