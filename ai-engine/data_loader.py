from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "transaction_id",
    "sender",
    "receiver",
    "amount_paise",
    "currency",
    "timestamp",
    "device_id",
    "ip_address",
    "status",
    "scenario_label",
}
VALID_STATUSES = {"SUCCESS", "DECLINED"}
VALID_SCENARIO_LABELS = {"NORMAL", "SYNTHETIC_SUSPICIOUS"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = PROJECT_ROOT / "java-engine" / "data" / "transactions.csv"


def load_transactions(path=DEFAULT_DATASET_PATH):
    """Load and validate a transaction CSV without changing the source file."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Transaction dataset not found: {source}")

    try:
        transactions = pd.read_csv(source)
    except pd.errors.EmptyDataError as error:
        raise ValueError(f"Transaction dataset is empty: {source}") from error
    except pd.errors.ParserError as error:
        raise ValueError(f"Could not parse transaction dataset {source}: {error}") from error

    transactions.columns = [str(column).strip() for column in transactions.columns]
    if "amount_paise" not in transactions.columns and "amountPaise" in transactions.columns:
        transactions = transactions.rename(columns={"amountPaise": "amount_paise"})

    missing_columns = sorted(REQUIRED_COLUMNS - set(transactions.columns))
    if missing_columns:
        raise ValueError(
            "Transaction dataset is missing required columns: "
            + ", ".join(missing_columns)
        )

    duplicate_ids = transactions["transaction_id"].duplicated(keep=False)
    if duplicate_ids.any():
        duplicate_values = sorted(
            transactions.loc[duplicate_ids, "transaction_id"].astype(str).unique()
        )
        raise ValueError(
            "Duplicate transaction_id value(s): " + ", ".join(duplicate_values)
        )

    text_columns = REQUIRED_COLUMNS - {"amount_paise", "timestamp"}
    for column in sorted(text_columns):
        values = transactions[column]
        missing = values.isna() | values.astype("string").str.strip().eq("")
        if missing.any():
            rows = (transactions.index[missing] + 2).tolist()
            raise ValueError(
                f"Missing value in required column '{column}' at CSV row(s): {rows}"
            )

    amounts = pd.to_numeric(transactions["amount_paise"], errors="coerce")
    invalid_amounts = (
        amounts.isna()
        | amounts.isin([float("inf"), float("-inf")])
        | amounts.le(0)
        | amounts.mod(1).ne(0)
    )
    if invalid_amounts.any():
        rows = (transactions.index[invalid_amounts] + 2).tolist()
        raise ValueError(
            f"Invalid amount_paise value at CSV row(s): {rows}; "
            "amounts must be positive whole-number paise."
        )
    transactions["amount_paise"] = amounts.astype("int64")

    timestamps = pd.to_datetime(transactions["timestamp"], errors="coerce", utc=True)
    invalid_timestamps = timestamps.isna()
    if invalid_timestamps.any():
        rows = (transactions.index[invalid_timestamps] + 2).tolist()
        raise ValueError(f"Invalid timestamp at CSV row(s): {rows}")
    transactions["timestamp"] = timestamps

    invalid_statuses = ~transactions["status"].isin(VALID_STATUSES)
    if invalid_statuses.any():
        values = sorted(transactions.loc[invalid_statuses, "status"].unique().tolist())
        raise ValueError(f"Invalid transaction status value(s): {values}")

    invalid_labels = ~transactions["scenario_label"].isin(VALID_SCENARIO_LABELS)
    if invalid_labels.any():
        values = sorted(
            transactions.loc[invalid_labels, "scenario_label"].unique().tolist()
        )
        raise ValueError(f"Invalid scenario label value(s): {values}")

    return transactions


def print_summary(transactions):
    """Display basic transaction counts and the status distribution."""
    print(f"Transaction count: {len(transactions)}")
    print(f"Unique senders: {transactions['sender'].nunique()}")
    print(f"Unique receivers: {transactions['receiver'].nunique()}")
    for column in ("status", "scenario_label"):
        if column in transactions.columns:
            print(f"{column.replace('_', ' ').title()} distribution:")
            print(
                transactions[column]
                .value_counts(dropna=False)
                .sort_index()
                .to_string()
            )


def main():
    try:
        transactions = load_transactions()
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print_summary(transactions)


if __name__ == "__main__":
    main()
