from __future__ import annotations

import os
from pathlib import Path
import shutil

TEST_DATA = Path("/tmp/wizard_wnba_test_data")
if TEST_DATA.exists():
    shutil.rmtree(TEST_DATA)
os.environ["DATA_DIR"] = str(TEST_DATA)
os.environ["ENVIRONMENT"] = "test"
