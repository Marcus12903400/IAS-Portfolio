import sys

# Print before importing anything heavy so a slow disk (e.g. an iCloud-offloaded
# environment) never looks like a hung, blank window.
print("AutoDeck starting...", flush=True)

from .cli import main  # noqa: E402

sys.exit(main())
