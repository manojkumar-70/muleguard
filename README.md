# MuleGuard AI

MuleGuard AI is a student cybersecurity project for detecting suspicious synthetic financial transaction networks. It will combine rule-based analysis, machine learning, graph analysis, and explainable risk scoring.

The project is planned to use Java for payment simulation, Python for AI and data analysis (Pandas, NumPy, Scikit-learn, and NetworkX), FastAPI for the backend API, and Streamlit for the dashboard.

## Synthetic analysis API

The local FastAPI backend analyzes only synthetic transactions. It does not
connect to payment services or perform account actions. From `ai-engine`, start
the server with:

```powershell
python -m pip install -r .\requirements.txt
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

The API provides `GET /health`, `GET /summary`, `GET /alerts`,
`GET /accounts/{account_id}`, and `POST /analyze`. Run its tests from
`ai-engine` with `python -m unittest -v test_api`.

## Local dashboard

The Streamlit dashboard uses the same local synthetic CSV and analysis modules.
From the project root, install the AI-engine requirements and launch it with:

```powershell
python -m pip install -r .\MuleGuardAI\ai-engine\requirements.txt
python -m streamlit run .\MuleGuardAI\dashboard\app.py
```

The dashboard defaults to `http://localhost:8501`. Run its tests from the
`dashboard` directory with `python -m unittest -v test_dashboard`.
