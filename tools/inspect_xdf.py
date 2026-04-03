#!/usr/bin/env python3
"""
Command-line wrapper for the XDF inspector.
"""

import sys

from labrecorder.xdf.inspector import inspect_xdf_file


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python tools/inspect_xdf.py <xdf_file>")
        sys.exit(1)

    sys.exit(0 if inspect_xdf_file(sys.argv[1]) else 1)
