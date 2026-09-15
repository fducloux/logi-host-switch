import sys

from logi_switch.app import run

if __name__ == "__main__":
    run(use_tray="--no-tray" not in sys.argv)
