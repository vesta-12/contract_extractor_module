import json
from pathlib import Path
from contract_extractor.visualization import (
    ContractResultVisualizer,
    VisualizationConfig,
)

SOURCE_PDF_PATH = Path(
    "data/input/test_loan_agreement_anonymized.pdf"
)

RESULT_JSON_PATH = Path(
    "data/output/test_loan_agreement_result.debug.json"
)

OUTPUT_DIR = Path(
    "data/output/visualization"
)


def main() -> None:
    visualizer = ContractResultVisualizer(
        VisualizationConfig(
            dpi=170,
            line_width=2,
            fill_alpha=30,
            label_mode="numbered",
            legend_mode="side",

            font_size=11,
            badge_font_size=10,
            legend_font_size=11,
            legend_title_font_size=15,

            draw_page_header=True,
            draw_confidence_in_legend=False,
            draw_owner_in_legend=True,

            legend_width_ratio=0.42,
            legend_max_value_length=78,

            create_clean_images=True,
            create_review_images=True,
            create_clean_pdf=True,
            create_review_pdf=True,
            create_summary_json=True,
        )
    )

    summary = visualizer.render_pdf(
        source_pdf_path=SOURCE_PDF_PATH,
        result_json_path=RESULT_JSON_PATH,
        output_dir=OUTPUT_DIR,
    )

    print("визуализация завершена")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()