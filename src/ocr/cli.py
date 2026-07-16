from __future__ import annotations

import argparse
import json
from pathlib import Path

from ocr.config import OCRConfig
from ocr.service import TesseractOCRProcessor


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Локальное OCR PDF в JSON schema 3.0-production"
    )
    parser.add_argument("input_pdf", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--dpi", type=int, default=OCRConfig().dpi)
    parser.add_argument("--lang", default=OCRConfig().language)
    parser.add_argument(
        "--timeout",
        type=int,
        default=OCRConfig().timeout_seconds,
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=OCRConfig().workers,
    )
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    processor = TesseractOCRProcessor(
        OCRConfig(
            dpi=arguments.dpi,
            language=arguments.lang,
            timeout_seconds=arguments.timeout,
            workers=arguments.workers,
            pretty_json=arguments.pretty,
        )
    )
    result = processor.process(
        arguments.input_pdf,
        arguments.output_json,
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "source_file": result["meta"]["file"],
                "page_count": result["meta"]["pages"],
                "workers": result["meta"]["workers"],
                "quality": result["quality"],
                "timing": result["timing"],
                "output_json": str(arguments.output_json),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
