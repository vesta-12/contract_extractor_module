# Document Processing Application

Локальное веб-приложение принимает один или несколько PDF, запускает существующий Tesseract OCR, передаёт актуальный OCR JSON в `contract_extractor`, сохраняет найденные сущности с координатами и формирует PDF с подсветкой. Для каждого задания доступны отдельный итоговый JSON и общий ZIP.

Алгоритмы OCR, восстановления layout, извлечения сущностей, linking и визуализации не переписаны. Новый слой отвечает только за orchestration, хранение, фоновые статусы, HTTP API и пользовательский интерфейс.

## Быстрый запуск

Требования:

- Python 3.12+;
- Tesseract OCR 5 с языками `rus` и `eng`;
- системная команда `tesseract` в `PATH` либо `TESSERACT_CMD` в окружении.

Установка и запуск в PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:PYTHONPATH = "src"
python -m server
```

Откройте [http://127.0.0.1:8000](http://127.0.0.1:8000). Статический frontend обслуживается тем же FastAPI-сервером, поэтому отдельный frontend-процесс не нужен.

Вариант с Docker, уже включающий Tesseract `rus+eng`:

```powershell
docker build -t contract-extractor-app .
docker run --rm --name contract-extractor -p 8000:8000 contract-extractor-app
```

Не запускайте одновременно локальный `python -m server` и Docker-контейнер
на порту `8000`. На Windows локальный процесс, слушающий
`127.0.0.1:8000`, может перехватить запросы браузера, даже если Docker
публикует `0.0.0.0:8000`. После запуска проверьте
`http://127.0.0.1:8000/api/health`: статус должен быть `ready`, а в
`components.ocr.missing_languages` должен быть пустой список. Если нужны оба
сервера, опубликуйте контейнер на другом порту, например
`docker run --rm --name contract-extractor -p 8001:8000 contract-extractor-app`,
и откройте `http://127.0.0.1:8001`.

Docker-образ уже содержит всё приложение: frontend, FastAPI, OCR и Tesseract.
Локальный `python -m server` для Docker-варианта запускать не требуется.
Серверы на разных портах остаются двумя независимыми приложениями: frontend
использует относительные пути `/api/...` и всегда обращается к тому же порту,
с которого была открыта страница. Поэтому страница на `8090` использует
локальный OCR, а не Tesseract из контейнера на `8000`.

## Пайплайн

```text
upload PDF
  -> проверка имени, расширения, MIME, заголовка и размера
  -> изолированный data/jobs/<job_id>/documents/<document_id>/source
  -> TesseractOCRProcessor -> OCR JSON 3.0-production
  -> OCRDocumentLoader -> восстановление layout
  -> RuleEngine -> EntityResolver -> PartyLinker
  -> production/debug extraction JSON
  -> ContractResultVisualizer -> PDF с подсветкой
  -> итоговый result.json -> ZIP задания
```

`DocumentProcessingService.process_file(path)` выполняет один документ. `process_files(paths)` обрабатывает набор путей последовательно и возвращает независимый результат каждого файла. HTTP-версия использует тот же сервис через `JobManager`.

## Структура приложения

```text
frontend/                       статический HTML/CSS/JavaScript UI
src/
  api/                          FastAPI factory, маршруты и ответы
  document_processing/
    config.py                   переменные окружения
    models.py                   статусы, ошибки, итоговая summary-модель
    files.py                    безопасные имена и metadata-валидация
    uploads.py                  потоковое сохранение с лимитом размера
    storage.py                  файловый JobStore и защита путей
    jobs.py                     фоновый lifecycle заданий
    archive.py                  общий ZIP и manifest
    pipeline.py                 OCR -> extractor -> visualization
    logging_config.py           console + rotating file log
  ocr/
    config.py                   неизменяемые OCR defaults и предел workers
    service.py                  стабильная обёртка над process_pdf
    cli.py                      OCR CLI
    ocr_document.py             существующий OCR-алгоритм
  contract_extractor/           существующие модели и бизнес-логика
  server.py                     точка запуска FastAPI/uvicorn
tests/                          unit, orchestration и API-тесты
```

Подробное направление зависимостей описано в `ARCHITECTURE.md`, исходное состояние — в `PROJECT_CONTEXT.md`, итог рефакторинга — в `REFACTORING_REPORT.md`.

## Конфигурация

Все доступные параметры перечислены в `.env.example`:

- `APP_DATA_DIR`, `APP_FRONTEND_DIR`;
- `APP_MAX_FILE_SIZE_MB`, `APP_MAX_FILES`;
- `APP_ALLOWED_EXTENSIONS`, `APP_ALLOWED_MIME_TYPES`;
- `APP_JOB_WORKERS`, `APP_RETENTION_HOURS`;
- `APP_HOST`, `APP_PORT`, `APP_DEVELOPMENT`, `APP_LOG_LEVEL`;
- `OCR_DPI`, `OCR_LANGUAGE`, `OCR_TIMEOUT_SECONDS`, `OCR_WORKERS`, `OCR_PRETTY_JSON`;
- опционально `TESSERACT_CMD`.

OCR по-прежнему ограничивает число page-processes значением `4`. `APP_JOB_WORKERS` ограничивает независимую конкуренцию заданий и по умолчанию равен `1`, чтобы несколько заданий не умножали OCR-нагрузку бесконтрольно.

## API

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/api/health` | готовность OCR/extractor/visualizer и лимиты загрузки |
| `POST` | `/api/documents/process` | multipart `files`, один или несколько PDF; ответ `202` |
| `GET` | `/api/jobs/{job_id}` | polling статуса задания и каждого документа |
| `GET` | `/api/documents/{document_id}/download` | PDF с подсветкой |
| `GET` | `/api/documents/{document_id}/json` | итоговый JSON с OCR и extraction |
| `GET` | `/api/jobs/{job_id}/download` | ZIP всех доступных результатов |

Пример:

```bash
curl -F "files=@contract.pdf;type=application/pdf" \
  http://127.0.0.1:8000/api/documents/process
```

Статусы задания и документа: `queued`, `processing`, `completed`, `partially_completed`, `failed`. Ошибка одного файла не отменяет результаты остальных. Если OCR завершился, а extraction или annotation упали, OCR JSON и доступная часть результата сохраняются со статусом `partially_completed`.

Публичный polling-ответ содержит только краткие сущности, статусы, предупреждения и download URL. Полный OCR JSON доступен в скачиваемом `result.json`, но не размножается в каждом polling-ответе.

## Хранение и логи

```text
data/
  jobs/<job_id>/
    manifest.json
    documents/<document_id>/
      source/<document_id>.pdf
      ocr/ocr.json
      results/result.json
      results/extraction.debug.json
      results/extraction.production.json
      annotated/annotated_document.clean.pdf
    archive/results.zip
  logs/application.log
```

Исходное имя используется только как отображаемое безопасное имя. Физический путь строится из UUID, поэтому одинаковые имена не конфликтуют. При запуске удаляются задания, у которых `updated_at` старше `APP_RETENTION_HOURS`.

Логи содержат job/document ID, этапы и длительность, но не полный текст документа и не содержимое файлов.

## Тесты

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests -q
```

API-тесты используют тестовую реализацию тяжёлого OCR-компонента. Orchestration-тесты передают сохранённый production OCR JSON в реальный extractor и проверяют 31 сущность, отсутствие сущностей, отклонение изображений, частичное сохранение после extraction/annotation errors, безопасные пути, ZIP, одинаковые имена и параллельные запросы.

Полный реальный E2E удобно повторить через Docker: запустить контейнер, загрузить `data/input/raw/test_loan_agreement_anonymized.pdf` в UI или через API и дождаться `completed`. Ожидается 2 страницы, OCR schema `3.0-production`, 31 сущность, итоговый JSON, annotated PDF и ZIP.

## Известные ограничения

- Текущий OCR entrypoint называется `process_pdf` и поддерживает только PDF; изображения намеренно не подключены через фиктивный адаптер.
- Очередь — локальный `ThreadPoolExecutor`, а manifests — файлы. После аварийного перезапуска готовые результаты остаются, но незавершённые задания автоматически не возобновляются.
- Приложение рассчитано на локальный доверенный запуск и не содержит аутентификацию или multi-tenant ACL.
- OCR остаётся CPU-intensive; для production потребуется внешняя очередь и ограничение ресурсов контейнера.
- `src/ocr/ocr_document.py` остаётся крупным алгоритмическим модулем: его механическое разделение отложено до появления OCR regression fixtures для разных layout-сценариев.

# Внутреннее устройство Contract Extractor

Ниже сохранён подробный справочник исходного extractor-модуля. Он принимает production OCR JSON, находит юридические и банковские сущности, связывает их со сторонами договора и сохраняет координаты для подсветки.

## 1. Общая логика

```text
Исходный PDF
    ↓
OCR-пайплайн
    ↓
OCR JSON: страницы, слова, bbox, confidence
    ↓
OCRDocumentLoader
    ↓
OCRDocument / OCRPage / OCRWord
    ↓
LayoutLineBuilder + SpatialSearch
    ↓
RuleEngine + ExtractionRule
    ↓
EntityCandidate[]
    ↓
EntityResolver
    ↓
чистый список сущностей
    ↓
PartyLinker
    ↓
ContractParty[]
    ↓
ContractExtractionResult
    ↓
debug JSON / production JSON
    ↓
ContractResultVisualizer
    ↓
clean PDF / review PDF
```

Главный принцип архитектуры: каждый слой отвечает только за одну задачу.

- OCR распознаёт текст.
- `input` приводит OCR JSON к внутреннему формату.
- `layout` восстанавливает структуру страницы.
- `rules` ищут возможные сущности.
- `resolution` удаляет дубли и конфликты.
- `linking` связывает сущности со сторонами.
- `visualization` отображает найденное на PDF.

---

## 2. Требования к OCR JSON

Extractor не зависит от конкретной OCR-модели, но зависит от входного контракта.

Минимально для каждого слова нужны:

```json
{
  "text": "БИН",
  "bbox": [0.10, 0.20, 0.15, 0.22],
  "confidence": 0.99
}
```

Нужно сохранять:

- номер страницы;
- текст каждого отдельного слова;
- координаты каждого слова;
- правильный порядок слов;
- желательно OCR confidence.

Формат bbox:

```text
[x1, y1, x2, y2]
```

Рекомендуется использовать нормализованные координаты `0.0–1.0`.

Если OCR будет заменён, остальные слои менять не нужно, пока новый результат преобразуется в этот формат.

```text
Новый OCR JSON
→ adapter/input
→ OCRDocument
→ существующий extractor
```

---

## 3. Структура проекта

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

---

# 4. Слой `models`

Этот слой содержит общие структуры данных. Он ничего не извлекает и не принимает решений.

## `BoundingBox`

Представляет прямоугольник на странице:

```python
BoundingBox(
    x1=0.10,
    y1=0.20,
    x2=0.30,
    y2=0.24,
)
```

Используется для:

- хранения координат слова;
- объединения нескольких слов;
- расчёта центра;
- определения пересечения;
- расчёта IoU;
- пространственного сравнения;
- визуализации.

Все правила и последующие слои работают через `BoundingBox`, а не через необработанные списки координат.

## `OCRWord`

Представляет одно слово из OCR.

Обычно содержит:

```text
id
text
normalized_text
page
bbox
confidence
index
```

Пример ID:

```text
p2-w188
```

Это слово №188 на странице 2.

`OCRWord` является основной единицей поиска. Точные координаты итоговой сущности строятся из координат входящих в неё слов.

## `OCRPage`

Представляет одну страницу документа.

Содержит:

- номер страницы;
- список слов;
- при наличии регионы, строки или дополнительные OCR-данные;
- размеры или метаданные страницы.

## `OCRDocument`

Представляет весь распознанный документ.

Содержит страницы и общие метаданные. Именно этот объект передаётся в layout-слой и правила.

## `LayoutLine`

Представляет восстановленную строку документа.

Содержит:

```text
id
page
words
bbox
text
region
```

Строка нужна, потому что большинство значений определяются не по одному слову, а по контексту:

```text
БИН: 123456789012
```

или:

```text
в лице генерального директора Иванова Ивана Ивановича
```

## `EntityCandidate`

Главный объект результата отдельного правила.

Пример:

```python
EntityCandidate(
    id="candidate-bin-p2-w188",
    entity_type="bin",
    value="123456789012",
    raw_value="123456789012,",
    page=2,
    bbox=...,
    word_ids=("p2-w188",),
    confidence=1.0,
    rule_id="bin_rule",
)
```

Основные поля:

```text
id
entity_type
value
raw_value
page
bbox
word_ids
confidence
rule_id
region
line_id
anchor_word_ids
validation
evidence
metadata
```

### `value`

Нормализованное значение, которое используется сервисом.

### `raw_value`

Первоначальный OCR-текст. Нужен для диагностики и проверки OCR-коррекций.

### `word_ids`

Список OCR-слов, из которых была собрана сущность.

### `confidence`

Эвристическая уверенность правила. Это не математически откалиброванная вероятность.

### `validation`

Результат формальной проверки:

```text
длина БИН;
формат IBAN;
MOD 97;
формат SWIFT;
количество цифр.
```

### `evidence`

Объяснение, почему кандидат был принят:

```text
найден якорь;
значение справа от якоря;
совпала длина;
подходит контекст;
совпала колонка.
```

---

# 5. Слой `input`

Слой преобразует внешний OCR JSON во внутренние модели.

## `OCRDocumentLoader`

Главный класс загрузки.

Отвечает за:

1. чтение JSON;
2. проверку структуры;
3. нормализацию координат;
4. создание `OCRWord`;
5. создание `OCRPage`;
6. создание `OCRDocument`;
7. генерацию стабильных ID.

Остальные слои не должны знать, как именно выглядел исходный JSON.

## `validate_ocr_payload`

Проверяет вход до запуска правил:

- существуют ли страницы;
- присутствуют ли слова;
- корректен ли bbox;
- является ли номер страницы допустимым;
- является ли текст строкой;
- находится ли confidence в допустимом диапазоне.

Если коллега изменит формат OCR JSON, адаптация должна происходить здесь, а не внутри правил.

---

# 6. Слой `layout`

Layout-слой восстанавливает структуру документа из отдельных OCR-слов.

## `LayoutLineBuilder`

Группирует слова в строки.

Логика обычно учитывает:

- близкое вертикальное положение;
- высоту слов;
- расстояние между словами;
- порядок слева направо;
- принадлежность к колонке или региону.

Результат:

```text
OCRWord[]
→ LayoutLine[]
```

Например, отдельные слова:

```text
БИН
123456789012
```

становятся одной строкой:

```text
БИН 123456789012
```

## `SpatialSearch`

Предоставляет правилам удобный поиск по расположению.

Через него можно:

- найти строку по слову;
- найти слова справа от якоря;
- получить следующую строку;
- найти ближайший объект;
- проверить нахождение в одной колонке;
- получить контекст вокруг кандидата;
- сравнить вертикальное и горизонтальное расстояние.

Правила не должны вручную перебирать весь документ. Они задают пространственный запрос через `SpatialSearch`.

---

# 7. Слой `rules`

Каждое правило ищет один тип сущности.

## `ExtractionRule`

Базовый класс всех правил.

Общий контракт:

```python
class ExtractionRule:
    rule_id: str
    entity_type: str

    def extract(
        self,
        document,
        spatial,
    ) -> tuple[EntityCandidate, ...]:
        ...
```

Правило:

1. получает документ и пространственный индекс;
2. ищет якоря, форматы и контекст;
3. создаёт `EntityCandidate`;
4. не связывает кандидата со стороной;
5. не удаляет конфликты других правил.

## `BINRule`, `IINRule`, `INNRule`

Файл: `tax_ids.py`.

Ищут налоговые идентификаторы.

Пример логики `BINRule`:

```text
найти слово "БИН"
→ посмотреть значение справа или ниже
→ убрать разделители
→ проверить 12 цифр
→ объединить bbox цифр
→ создать EntityCandidate(type="bin")
```

## `BIKRule`, `BICSWIFTRule`, `KZIBANRule`, `BankAccountRule`

Файл: `bank_details.py`.

Ищут банковские реквизиты.

Дополнительно могут выполнять безопасные OCR-коррекции:

```text
O → 0
I → 1
```

Но только там, где формат поля это допускает.

`KZIBANRule` отдельно проверяет:

- начало `KZ`;
- длину;
- допустимые символы;
- MOD 97.

## `DateRule`

Файл: `document_values.py`.

Ищет даты в разных форматах:

```text
17.06.2026
17/06/2026
17 июня 2026 года
«17» июня 2026 г.
```

Нормализует:

```text
2026-06-17
```

Через контекст может записать назначение даты в `metadata`:

```text
contract_date
repayment_due_date
interest_start_date
power_of_attorney_date
```

## `MoneyAmountRule`

Ищет суммы и валюты.

Пример:

```text
1 000 000,00 долларов США
```

Результат:

```text
value = "1000000.00"
metadata.currency = "USD"
```

## `PercentageRule`

Ищет проценты:

```text
4 %
4%
четыре процента
```

Через контекст может определить:

```text
annual_interest_rate
penalty_rate
commission_rate
```

## `OrganizationRule`

Файл: `legal_parties.py`.

Ищет организации по формам:

```text
ТОО
ООО
АО
ИП
LLP
LLC
```

Возвращает название организации и возможную подсказку роли.

## `BankNameRule`

Ищет названия банков.

Использует более строгий контекст, чем `OrganizationRule`:

- слово `банк`;
- банковский блок;
- реквизиты рядом;
- метки `Банк`, `Банк-корреспондент`;
- многострочное название.

Это предотвращает распознавание любой организации как банка.

## `PersonNameRule`

Ищет ФИО:

```text
Иванов Иван Иванович
Иванов И.И.
```

Использует:

- последовательность слов;
- окончания;
- слова `в лице`, `директор`, `представитель`;
- подписи в реквизитах.

## `PositionRule`

Ищет должности:

```text
генеральный директор
представитель
председатель правления
```

Может нормализовать падеж:

```text
генерального директора
→ генеральный директор
```

## `AddressRule`

Файл: `addresses.py`.

Ищет адрес по комбинации признаков:

```text
индекс;
страна;
город;
улица;
проспект;
дом;
офис;
район.
```

Адрес может состоять из нескольких строк. В таком случае правило объединяет слова и bbox.

---

# 8. `RuleEngine`

`RuleEngine` управляет запуском всех правил.

Он:

1. получает список `ExtractionRule`;
2. последовательно запускает каждое правило;
3. собирает все `EntityCandidate`;
4. проверяет базовую корректность кандидатов;
5. сохраняет ошибки отдельных правил;
6. возвращает общий список кандидатов.

При настройке:

```python
continue_on_error=True
```

ошибка одного правила не останавливает обработку документа.

Например, если упал `AddressRule`, БИН, IBAN и организации всё равно будут извлечены.

---

# 9. Слой `resolution`

Правила работают независимо, поэтому один участок текста может породить несколько кандидатов.

## `EntityResolver`

Получает:

```text
EntityCandidate[]
```

и возвращает:

```text
accepted_entities
rejected_candidates
```

Основные задачи:

### Удаление точных дублей

Сравниваются:

```text
entity_type
normalized value
page
word_ids
bbox
```

### Разрешение конфликтов

Примеры конфликтующих типов:

```text
bin / iin / inn
iban / bank_account
bik / bic_swift
organization / bank_name
```

Resolver сравнивает:

- confidence;
- validation;
- evidence;
- количество подтверждающих сигналов;
- точность формата;
- пересечение слов и bbox.

Лучший кандидат остаётся в основном результате. Остальные могут быть сохранены в `rejected_candidates` для отладки.

---

# 10. Слой `linking`

До этого этапа сущности являются плоским списком:

```text
organization
bin
address
person_name
bank_name
iban
swift
```

`PartyLinker` превращает этот список в стороны договора.

## `ContractParty`

Представляет одну сторону:

```text
role
organization
organization_occurrences
identifiers
addresses
representatives
bank_details
```

## `PartyRepresentative`

Представляет человека, подписывающего договор:

```text
name
position
name_occurrence_ids
position_occurrence_ids
```

Полное ФИО в тексте и сокращённая подпись могут быть объединены в одного представителя.

## `PartyBankDetails`

Представляет один банковский блок:

```text
bank_name
accounts
iban
bik
swift
is_correspondent
```

У одной стороны может быть несколько банковских блоков.

## `PartyLinker`

Основная логика:

1. группирует одинаковые организации;
2. определяет роль организации;
3. выбирает основное упоминание;
4. привязывает идентификаторы;
5. привязывает адреса;
6. привязывает представителей;
7. собирает банковские блоки.

Для связи используются:

- одна страница;
- одна колонка;
- близкое вертикальное положение;
- общий регион;
- заголовок стороны;
- role hint;
- контекст реквизитов.

Пример:

```text
Правая колонка:
ТОО «Компания»
Адрес
БИН
Банк
IBAN
SWIFT
```

Все эти сущности будут связаны с одной стороной.

---

# 11. `pipeline.py`

## `ContractExtractorConfig`

Настройки обработки.

Обычно включает:

```text
continue_on_rule_error
strict_candidate_validation
include_rejected_candidates
include_unassigned_entities
```

В сервисной версии сюда можно добавить:

```text
enabled_entity_types
enable_party_linking
```

## `ContractExtractor`

Главный управляющий класс.

Он создаёт и соединяет:

```text
loader
layout builder
spatial search
rule engine
resolver
party linker
```

Метод извлечения выполняет этапы:

```text
1. загрузка OCR JSON
2. создание внутренних моделей
3. построение layout
4. запуск правил
5. разрешение конфликтов
6. связывание сторон
7. создание итогового результата
```

## `extract_contract_data`

Упрощённая публичная функция:

```python
from contract_extractor import extract_contract_data

result = extract_contract_data(
    "data/input/document_ocr.json"
)
```

Она скрывает внутреннюю сборку pipeline и является основной точкой интеграции.

---

# 12. `ContractExtractionResult`

Финальный объект обработки.

Содержит:

```text
status
successful
source
document
parties
entities
unassigned_entities
rejected_candidates
warnings
issues
metadata
timings
```

## `to_dict()`

Формирует debug JSON.

Включает:

- полные кандидаты;
- evidence;
- validation;
- rejected candidates;
- warnings;
- служебную информацию.

Используется для разработки и анализа качества.

## `to_production_dict()`

Формирует компактный JSON.

Сущности хранятся один раз:

```text
entity_registry
```

Остальные блоки используют ссылки по ID.

Это уменьшает размер результата и подходит для API или базы данных.

---

# 13. Визуализация

## `VisualizationConfig`

Настраивает:

```text
dpi
толщину рамок
прозрачность
размер шрифта
режим подписей
режим легенды
создание PNG/PDF
```

## `VisualizationEntity`

Упрощённое представление сущности для renderer:

```text
id
entity_type
value
page
bbox
confidence
owner_role
owner_name
```

## `ContractResultVisualizer`

Принимает:

```text
исходный PDF
+
debug или production JSON
```

Создаёт:

```text
page_001.clean.png
page_001.review.png
annotated_document.clean.pdf
annotated_document.review.pdf
visualization_summary.json
```

### Clean

На документе только:

- рамка;
- прозрачная заливка;
- номер сущности.

### Review

Слева clean-страница, справа легенда:

```text
номер
тип
значение
сторона
```

### Фильтрация типов

Только БИН:

```python
visualizer.render_pdf(
    source_pdf_path=SOURCE_PDF_PATH,
    result_json_path=RESULT_JSON_PATH,
    output_dir=OUTPUT_DIR,
    include_entity_types={"bin"},
)
```

---

# 14. Точки запуска

## `main.py`

Локальный запуск extraction pipeline.

Обычно:

1. указывает путь к OCR JSON;
2. вызывает `extract_contract_data`;
3. сохраняет debug JSON;
4. сохраняет production JSON;
5. выводит краткую статистику.

Запуск:

```bash
python main.py
```

## `visualize_result.py`

Локальный запуск визуализации.

Указывает:

```text
SOURCE_PDF_PATH
RESULT_JSON_PATH
OUTPUT_DIR
```

Запуск:

```bash
python visualize_result.py
```

---

# 15. Как добавить новый тип сущности

Например, `contract_number`.

## Шаг 1

Создать правило:

```python
class ContractNumberRule(ExtractionRule):
    rule_id = "contract_number_rule"
    entity_type = "contract_number"

    def extract(self, document, spatial):
        ...
```

## Шаг 2

Добавить правило в список `RuleEngine`.

## Шаг 3

При необходимости добавить конфликтующие типы в `EntityResolver`.

## Шаг 4

Если поле относится к стороне, добавить linking-логику в `PartyLinker`.

## Шаг 5

Добавить цвет и название в `ContractResultVisualizer`.

Остальные слои менять не требуется.

---

# 16. Что можно менять в OCR

Можно менять:

```text
OCR-модель;
предобработку изображения;
многопоточность;
рендер PDF;
распознавание таблиц;
порядок OCR-этапов.
```

Extractor не сломается, если на входе по-прежнему есть:

```text
pages
words
text
bbox
page number
confidence
```

Если формат изменился, нужно менять только `input` или писать адаптер.

Правила, resolver, linker и visualization должны работать с внутренними моделями, а не с конкретным JSON OCR-провайдера.

---

# 17. Интеграция в сервис

Текущий проект — вычислительное ядро, а не готовый backend.

Типовой сервисный процесс:

```text
POST /documents
→ сохранить PDF
→ запустить OCR
→ получить OCR JSON
→ extract_contract_data()
→ сохранить production JSON
→ при необходимости создать visualized PDF
→ вернуть статус и результат
```

Основной вызов:

```python
result = extract_contract_data(
    ocr_json_path
)

production_data = result.to_production_dict()
```

Визуализация запускается отдельно:

```python
visualizer.render_pdf(
    source_pdf_path=pdf_path,
    result_json_path=result_json_path,
    output_dir=output_dir,
    include_entity_types={"bin"},
)
```

# 18. Кратко об ответственности классов

```text
OCRDocumentLoader
    превращает внешний JSON во внутренние модели

BoundingBox
    хранит и сравнивает координаты

OCRWord / OCRPage / OCRDocument
    представляют распознанный документ

LayoutLineBuilder
    собирает слова в строки

SpatialSearch
    даёт правилам пространственный поиск

ExtractionRule
    базовый контракт правила

Конкретные Rule-классы
    создают EntityCandidate

RuleEngine
    запускает правила и собирает кандидатов

EntityResolver
    удаляет дубли и разрешает конфликты

PartyLinker
    связывает сущности со сторонами

ContractParty / PartyRepresentative / PartyBankDetails
    представляют структурированный результат

ContractExtractor
    управляет полным pipeline

ContractExtractionResult
    хранит итог и формирует JSON

ContractResultVisualizer
    рисует сущности на PDF
```
