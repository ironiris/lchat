#!/usr/bin/env python3
"""Compatibility entry point; use lchat.py for new installations."""

from lchat_web import *  # noqa: F401,F403
from lchat_web import main

if __name__ == "__main__":
    raise SystemExit(main())
