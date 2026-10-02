"""Local API for defensive analysis of synthetic MuleGuard AI transactions."""

from functools import lru_cache
import ipaddress
import re
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from anomaly_detection import detect_account_anomalies
from data_loader import load_transactions
from risk_aggregation import aggregate_risk
from rule_based_analysis import analyze_accounts
from transaction_graph_analysis import analyze_transaction_graph


DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
SYNTHETIC_LABELS = {"NORMAL", "SYNTHETIC_SUSPICIOUS"}

app = FastAPI(
    title="MuleGuard AI API",
    description=(
        "Defensive analysis of synthetic transactions only. Risk indicators "
        "are not proof of criminal activity and trigger no real account actions."
    ),
    version="0.1.0",
)


class SyntheticTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=5, max_length=80)
    sender: str = Field(min_length=5, max_length=80)
    receiver: str = Field(min_length=5, max_length=80)
    amount_paise: int = Field(gt=0)
    currency: str
    timestamp: datetime
    device_id: str = Field(min_length=5, max_length=80)
    ip_address: str
    status: str
    scenario_label: str

    @field_validator("transaction_id")
    @classmethod
    def validate_transaction_id(cls, value):
        if not re.fullmatch(r"TXN-[A-Za-z0-9-]+", value):
            raise ValueError("transaction_id must use the synthetic TXN- prefix")
        return value

    @field_validator("sender", "receiver")
    @classmethod
    def validate_account_id(cls, value):
        if not re.fullmatch(r"ACC-[A-Za-z0-9-]+", value):
            raise ValueError("account IDs must use the synthetic ACC- prefix")
        return value

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value):
        if not re.fullmatch(r"DEV-[A-Za-z0-9-]+", value):
            raise ValueError("device_id must use the synthetic DEV- prefix")
        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value):
        if value != "INR":
            raise ValueError("Only synthetic INR transactions are accepted")
        return value

    @field_validator("ip_address")
    @classmethod
    def validate_documentation_ip(cls, value):
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise ValueError("ip_address must be an IPv4 documentation address") from error
        if address.version != 4 or not any(
            address in network for network in DOCUMENTATION_NETWORKS
        ):
            raise ValueError(
                "Only reserved synthetic IPv4 documentation addresses are accepted"
            )
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value not in {"SUCCESS", "DECLINED"}:
            raise ValueError("status must be SUCCESS or DECLINED")
        return value

    @field_validator("scenario_label")
    @classmethod
    def validate_scenario_label(cls, value):
        if value not in SYNTHETIC_LABELS:
            raise ValueError(
                "scenario_label must be NORMAL or SYNTHETIC_SUSPICIOUS"
            )
        return value


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactions: list[SyntheticTransaction] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_unique_transaction_ids(self):
        transaction_ids = [
            transaction.transaction_id for transaction in self.transactions
        ]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise ValueError("transaction_id values must be unique")
        return self


def _run_analysis(transactions):
    rule_results = analyze_accounts(transactions)
    ml_results = detect_account_anomalies(transactions)
    graph_results = analyze_transaction_graph(transactions)
    risk_results = aggregate_risk(rule_results, ml_results, graph_results)

    return {
        "transaction_count": len(transactions),
        "rule_results": rule_results,
        "ml_results": ml_results.to_dict(orient="records"),
        "graph_results": graph_results,
        "risk_results": risk_results,
    }


@lru_cache(maxsize=1)
def _default_analysis():
    try:
        transactions = load_transactions()
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"Default synthetic transaction dataset is unavailable: {error}",
        ) from error
    return _run_analysis(transactions)


def _account_index(analysis):
    return {
        account["account_id"]: account
        for account in analysis["risk_results"]["accounts"]
    }


def _account_details(analysis, account_id):
    risk_account = _account_index(analysis).get(account_id)
    if risk_account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    rule_account = next(
        (
            account
            for account in analysis["rule_results"]
            if account["account_id"] == account_id
        ),
        {},
    )
    ml_account = next(
        (
            account
            for account in analysis["ml_results"]
            if account["account_id"] == account_id
        ),
        {},
    )
    graph_account = next(
        (
            account
            for account in analysis["graph_results"]["accounts"]
            if account["account_id"] == account_id
        ),
        {},
    )
    return {
        **risk_account,
        "rule_based_features": rule_account.get("features", {}),
        "anomaly_detection": {
            "prediction": ml_account.get("prediction"),
            "is_anomaly": ml_account.get("is_anomaly"),
            "anomaly_score": ml_account.get("anomaly_score"),
        },
        "graph_indicators": graph_account.get("risk_indicators", []),
    }


@app.get("/health")
def health():
    return {"status": "ok", "mode": "synthetic-only"}


@app.get("/summary")
def summary():
    analysis = _default_analysis()
    return {
        "transaction_count": analysis["transaction_count"],
        "graph": analysis["graph_results"]["summary"],
        "risk": analysis["risk_results"]["summary"],
    }


@app.get("/alerts")
def alerts():
    analysis = _default_analysis()
    flagged = [
        account
        for account in analysis["risk_results"]["accounts"]
        if account["risk_level"] in {"MEDIUM", "HIGH"}
    ]
    flagged.sort(key=lambda account: account["risk_score"], reverse=True)
    return {"count": len(flagged), "alerts": flagged}


@app.get("/accounts/{account_id}")
def account(account_id: str):
    return _account_details(_default_analysis(), account_id)


@app.post("/analyze")
def analyze(request: AnalyzeRequest):
    transactions = pd.DataFrame(
        [transaction.model_dump() for transaction in request.transactions]
    )
    try:
        analysis = _run_analysis(transactions)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "transaction_count": analysis["transaction_count"],
        "summary": analysis["risk_results"]["summary"],
        "graph_summary": analysis["graph_results"]["summary"],
        "accounts": analysis["risk_results"]["accounts"],
    }
