"""Allow running the tool as ``python -m md_ocr_cleaner``."""

import sys

from md_ocr_cleaner.cli import main

if __name__ == "__main__":
    sys.exit(main())
