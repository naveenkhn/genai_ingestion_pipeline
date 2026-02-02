# genai_ingestion_pipeline

This repository provides ingestion, indexing, and retrieval components for GenAI/RAG use cases, leveraging Azure AI Search, Azure OpenAI, Confluence, and code repositories.

## Scope
This repository contains reference implementations and ready-to-use helpers for:

- Confluence document discovery and retrieval
- Codebase ingestion (multi-language)
- Vector index creation (code and documents)
- Retrieval helpers for exploration
- Tree-sitter setup validation

## Structure
- `ingestion/`
- `indexing/`
- `retrieval/`
- `validation/`

The `validation/` directory contains component-level checks (e.g., Tree-sitter native bindings), not end-to-end pipeline validation.

## Configuration
A shared `.env` file at the repository root is used to configure environment variables. See `.env.example` for a template and guidance on required settings.

## License
MIT License.

## Attribution (optional)
If you find this useful, a mention or link back is appreciated but not required.
