# Third-Party Notices

Vaultline incorporates and relies on the following third-party open-source libraries, frameworks, and external APIs:

## Python Backend & MCP (`requirements.txt`, `mcp/requirements.txt`)
- **FastAPI** (`fastapi`) — MIT License
- **Uvicorn** (`uvicorn`) — BSD-3-Clause License
- **Pydantic** (`pydantic`) — MIT License
- **HTTPX** (`httpx`) — BSD-3-Clause License
- **pytest** (`pytest`) — MIT License
- **python-dotenv** (`python-dotenv`) — BSD-3-Clause License
- **Model Context Protocol Python SDK** (`mcp`) — MIT License
- **AnyIO** (`anyio`) — MIT License

## Frontend (`frontend/package.json`)
- **React** (`react`, `react-dom`) — MIT License
- **AG Studio** (`ag-studio`, `ag-studio-react`) — Commercial / Evaluation License (AG Grid Ltd; evaluation watermark applies when `VITE_AG_STUDIO_LICENSE_KEY` is unset)
- **Vite** (`vite`) — MIT License
- **TypeScript** (`typescript`) — Apache-2.0 License
- **Oxlint** (`oxlint`) — MIT License

## External Services & AI Models
- **PayPal REST API (Sandbox)** — Used via `PayPalSandboxAdapter` for refund capture verification and refund execution in Sandbox mode.
- **Google Gemini API** — Used by `mcp/vaultline_agent` (`gemini_client.py`) for structured refund request extraction from untrusted customer emails. All policy decisions (`ALLOW`, `ESCALATE`, `BLOCK`) remain strictly deterministic in the FastAPI backend.

