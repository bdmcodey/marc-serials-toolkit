#!/usr/bin/env python3
"""
Start the MARC Serials Toolkit on this machine.

    python run.py

Opens http://localhost:5003. Set MARC_PORT to use a different port; 5003 rather
than 5000 because macOS gives 5000 to AirPlay Receiver.

Installing the package (`pip install -e .`) also provides a `marc-serials`
command that does exactly this.
"""

from marc_serials.webapp import main

if __name__ == "__main__":
    main()
