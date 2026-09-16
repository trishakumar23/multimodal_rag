# Multimodal document ingestion

A take-home project for ingesting TotalEnergies annual reports into a relational
database, preserving text, tables, images, and page references.

## Development setup

Use Python 3.12. From the repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python --version
```

The application and its dependencies have not been implemented yet. Dependency
installation, API usage, tests, and Docker instructions will be added with the
implementation.

## Local files

Source PDFs are supplied separately and are not committed. Keep the four reports
locally as `report_2022.pdf`, `report_2023.pdf`, `report_2024.pdf`, and
`report_2025.pdf` in the project root during development.

The virtual environment, secrets, databases, generated artifacts, and exploratory
inspection files are excluded from Git. The repository will contain application
source, dependency definitions, tests, Docker configuration, and documentation
needed to run and review the project.
