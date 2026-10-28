"""Load the plugin as a package without importing the AstrBot runtime."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
