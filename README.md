# DocuTable

> A lightweight desktop tool for extracting structured tables from text-based financial PDFs, powered by PyQt5 and PyMuPDF.

Supports dual-mode parsing (text-based / image-based PDFs), side-by-side data preview, page-level filtering, and one-click Excel export — designed for analysts who need to quickly pull tabular data from bank annual reports.

[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![PyQt5](https://img.shields.io/badge/PyQt5-5.15-blue)](https://pypi.org/project/PyQt5/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Features

- **Dual-mode PDF parsing** — Automatically detects and handles both text-based and image-based PDFs
- **Coordinate-based table extraction** — Uses PyMuPDF word-level coordinates to preserve row/column boundaries
- **Side-by-side preview** — Original PDF page and extracted table data displayed simultaneously
- **Page-level filtering** — Filter, browse, and inspect tables page by page
- **Excel export** — One-click export to structured `.xlsx` with multi-sheet support
- **Batch processing** — Process multi-page PDFs with progress tracking and history management
- **Graceful degradation** — Image-based PDFs without API key fall back to cached previews with empty Excel output (no crash)

---

## Screenshots

### 1. PDF Automatic Parsing
Dual-mode parsing with automatic detection of text-based vs. image-based pages.

![PDF Automatic Parsing](screenshots/1_PDF自动解析.png)

### 2. Data Comparison Preview
Original PDF page and extracted table data shown side-by-side with cell-level editing.

![Data Comparison Preview](screenshots/2_数据对比预览.png)

### 3. Parsing History
Manage past tasks with status filtering, reload, and one-click cleanup.

![Parsing History](screenshots/3_解析历史记录.png)

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| GUI Framework | PyQt5 |
| PDF Rendering | PyMuPDF (fitz) |
| Excel Export | openpyxl |
| Image OCR | Volcano Engine Doubao API (optional) |

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

**Note:** Configure your Doubao API key in `config/settings.json` for image-based PDF OCR. Without the key, the tool falls back gracefully to cached previews.

---

## Project Structure

```
DocuTable/
├── codes/
│   ├── ui/              # PyQt5 UI components
│   ├── processor/       # PDF processing logic
│   └── config/          # Settings & API key management
├── config/              # Local settings (not committed)
├── data/                # PDF cache and intermediate data
├── screenshots/         # UI screenshots
├── dist/                # Packaged executable (if built)
└── main.py              # Entry point
```

---

## Requirements

- Python 3.8+
- PyQt5 >= 5.15.0
- PyMuPDF >= 1.23.0
- openpyxl >= 3.1.0
