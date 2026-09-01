import os

# A developer's personal config/local.yaml must never change what the test
# suite asserts against (see autodeck.config.load_config).
os.environ.setdefault("AUTODECK_IGNORE_LOCAL_CONFIG", "1")
