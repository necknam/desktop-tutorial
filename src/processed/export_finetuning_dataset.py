"""Compatibility CLI for the grounded dataset exporter."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.pipelines.export_finetuning_dataset import main

if __name__ == "__main__":
    exit_code = main()
    if exit_code:
        sys.exit(exit_code)