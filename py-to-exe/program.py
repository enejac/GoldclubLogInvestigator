"""
Example program — replace this file with your own code, then run Build-ProgramExe.ps1
"""
import sys


def main() -> int:
    print("Hello from program.exe!")
    print(f"Running on Python {sys.version.split()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
