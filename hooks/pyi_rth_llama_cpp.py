# Runtime hook: make llama_cpp native DLLs discoverable in the frozen onefile extract.
# PyInstaller extracts to sys._MEIPASS; llama-cpp-python loads llama_cpp/lib/llama.dll.
#
# Also preload ggml / OpenMP deps so Windows can resolve imports when the PyPI wheel
# (or a mismatched layout) would otherwise raise "The specified module could not be found".

from __future__ import annotations

import ctypes
import os
import sys


def _setup_llama_dll_path() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return
    lib = os.path.join(base, "llama_cpp", "lib")
    if not os.path.isdir(lib):
        alt = os.path.join(base, "llama_cpp")
        if os.path.isdir(alt):
            lib = alt
        else:
            return
    path = os.environ.get("PATH", "")
    if lib.lower() not in path.lower():
        os.environ["PATH"] = lib + os.pathsep + path
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(lib)
        except OSError:
            pass
    os.environ.setdefault("LLAMA_CPP_LIB_PATH", lib)

    # Load dependency order used by current llama.cpp CPU builds.
    preload = (
        "libomp140.x86_64.dll",
        "ggml-base.dll",
        "ggml-cpu.dll",
        "ggml-cpu-x64.dll",
        "ggml.dll",
        "llama.dll",
    )
    for name in preload:
        full = os.path.join(lib, name)
        if not os.path.isfile(full):
            continue
        try:
            ctypes.WinDLL(full)
        except OSError:
            pass


_setup_llama_dll_path()
