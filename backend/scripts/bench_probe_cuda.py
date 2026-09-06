#!/usr/bin/env python3
"""WSL CUDA probe — prints GPU visibility for the WSL python the backend uses."""
try:
    import torch
    print("torch:", torch.__version__)
    print("cuda available:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("device:", torch.cuda.get_device_name(0))
        print("vram_total_mb:", torch.cuda.get_device_properties(0).total_memory // 1048576)
        print("vram_free_mb:", torch.cuda.mem_get_info(0)[0] // 1048576)
except Exception as exc:
    print("torch probe failed:", exc)

try:
    import ctranslate2
    print("ctranslate2:", ctranslate2.__version__,
          "cuda_support:", ctranslate2.get_cuda_device_count() if hasattr(ctranslate2, "get_cuda_device_count") else "?")
except Exception as exc:
    print("ctranslate2 probe failed:", exc)
