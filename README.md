# Contract Extractor

Модуль принимает готовый OCR JSON, извлекает сущности из договоров, связывает их со сторонами и сохраняет координаты для подсветки на исходном PDF.

OCR внутри модуля не запускается.

## Общий процесс

```text
PDF
→ OCR
→ OCR JSON со словами и координатами
→ contract_extractor
→ debug JSON
→ production JSON
→ PDF с подсветкой
```

## Требования к OCR JSON

Для каждого слова нужны:

```json
{
  "text": "БИН",
  "bbox": [0.10, 0.20, 0.15, 0.22],
  "confidence": 0.99
}
```

Обязательно должны сохраняться:

- номер страницы;
- текст на уровне отдельных слов;
- координаты каждого слова;
- порядок слов;
- желательно OCR confidence.

Координаты используются в формате:

```text
[x1, y1, x2, y2]
```

Рекомендуемый диапазон — `0.0–1.0`.

Если OCR будет заменён, extractor продолжит работать без изменений, пока сохраняется этот контракт. Если формат изменится, достаточно добавить адаптер в слое `input`.

## Структура проекта

```text
src/contract_extractor/
├── input/
├── layout/
├── models/
├── rules/
├── resolution/
├── linking/
├── visualization/
├── pipeline.py
└── __init__.py

main.py
visualize_result.py
```

### `input/`

Загрузка и проверка OCR JSON.

Основные элементы:

```text
OCRDocumentLoader
load_ocr_document()
validate_ocr_payload()
```

### `models/`

Внутренние модели:

```text
BoundingBox
OCRDocument
OCRPage
OCRWord
LayoutLine
EntityCandidate
ContractParty
PartyRepresentative
PartyBankDetails
ContractExtractionResult
```

`EntityCandidate` хранит тип, значение, исходный OCR-текст, страницу, bbox, confidence, validation, evidence и metadata.

### `layout/`

Восстанавливает строки и структуру страницы.

```text
LayoutLineBuilder
SpatialSearch
```

Используется для поиска текста справа, ниже, в той же строке или колонке.

### `rules/`

Правила извлечения.

```text
tax_ids.py
    BINRule
    IINRule
    INNRule

bank_details.py
    BIKRule
    BICSWIFTRule
    KZIBANRule
    BankAccountRule

document_values.py
    DateRule
    MoneyAmountRule
    PercentageRule

legal_parties.py
    OrganizationRule
    BankNameRule
    PersonNameRule
    PositionRule

addresses.py
    AddressRule
```

Все правила наследуются от `ExtractionRule` и возвращают `EntityCandidate`.

### `rules/engine.py`

`RuleEngine` запускает правила, собирает кандидатов и сохраняет ошибки отдельных правил.

### `resolution/`

`EntityResolver`:

- удаляет дубли;
- проверяет пересечения bbox и word IDs;
- разрешает конфликты типов;
- переносит проигравшие варианты в `rejected_candidates`.

### `linking/`

`PartyLinker`:

- группирует повторные упоминания организаций;
- определяет роли сторон;
- связывает БИН, ИНН, адреса, представителей и банковские реквизиты;
- разделяет основной и корреспондентский банк.

### `pipeline.py`

Главная точка обработки.

```python
from contract_extractor import extract_contract_data

result = extract_contract_data(
    "data/input/document_ocr.json"
)
```

Внутри выполняется:

```text
load
→ layout
→ rules
→ resolution
→ linking
→ result
```

### `visualization/`

Рисует найденные сущности поверх исходного PDF.

Создаёт:

```text
annotated_document.clean.pdf
annotated_document.review.pdf
```

`clean` содержит рамки и номера.

`review` содержит страницу и боковую легенду.

## Запуск извлечения

В `main.py` указывается входной OCR JSON.

```bash
python main.py
```

Результаты:

```text
data/output/document_result.debug.json
data/output/document_result.json
```

### Debug JSON

Содержит:

- evidence;
- context;
- rejected candidates;
- warnings;
- полные метаданные.

### Production JSON

Компактная версия для сервиса:

- сущности хранятся в `entity_registry`;
- стороны и поля документа ссылаются на сущности по ID;
- объекты не дублируются.

## Запуск визуализации

В `visualize_result.py` задаются:

```python
SOURCE_PDF_PATH
RESULT_JSON_PATH
OUTPUT_DIR
```

Запуск:

```bash
python visualize_result.py
```

Результаты:

```text
page_001.clean.png
page_001.review.png
annotated_document.clean.pdf
annotated_document.review.pdf
visualization_summary.json
```

Для корректной подсветки должен использоваться тот же PDF, по которому был выполнен OCR.

## Подсветка только выбранных сущностей

Только БИН:

```python
visualizer.render_pdf(
    source_pdf_path=SOURCE_PDF_PATH,
    result_json_path=RESULT_JSON_PATH,
    output_dir=OUTPUT_DIR,
    include_entity_types={"bin"},
)
```

Несколько типов:

```python
include_entity_types={
    "bin",
    "organization",
    "iban",
}
```

## Получение сущностей из результата

```python
result = extract_contract_data(
    "data/input/document_ocr.json"
)

bins = result.entities_by_type("bin")

for item in bins:
    print(item.value, item.page, item.bbox)
```

## Поддерживаемые типы

```text
date
money_amount
percentage
organization
bank_name
person_name
position
address
bin
iin
inn
bik
bic_swift
iban
bank_account
```

## Зависимости

```bash
pip install pymupdf pillow
```

Остальные зависимости зависят от окружения проекта и OCR-пайплайна.

## Ограничения

- Модуль протестирован на ограниченном количестве документов.
- Confidence является эвристической оценкой.
- Внешние реестры организаций и банков не используются.
- Визуализация зависит от совпадения PDF и OCR-координат.
- Для документов с новой структурой могут понадобиться дополнительные правила.

## Что остаётся сделать в сервисе

Текущий проект — ядро обработки, а не готовый backend.

Для полноценного сервиса нужно добавить:

```text
API
загрузку PDF
запуск OCR
очередь задач
хранение результатов
авторизацию
логирование
обработку временных файлов
мониторинг
контейнеризацию
```

Основной вызов для интеграции:

```python
result = extract_contract_data(
    ocr_json_path
)

production_json = result.to_production_dict()
```

Визуализация подключается отдельно и запускается только при необходимости.
