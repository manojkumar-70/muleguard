"""Local Streamlit dashboard for synthetic MuleGuard AI transaction analysis."""

from pathlib import Path
import sys

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AI_ENGINE_PATH = PROJECT_ROOT / "ai-engine"
if str(AI_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_PATH))

from anomaly_detection import detect_account_anomalies
from data_loader import load_transactions
from risk_aggregation import DISCLAIMER, aggregate_risk
from rule_based_analysis import analyze_accounts
from transaction_graph_analysis import analyze_transaction_graph
from investigation_client import (
    InvestigationAPIClient,
    InvestigationAPIError,
)
from payment_service.schemas import HumanReviewDecision


OFFLINE_DATASET_MODE = "Offline Dataset"
INVESTIGATION_MODE = "Payment Service / Investigation"
DATA_SOURCE_MODES = (OFFLINE_DATASET_MODE, INVESTIGATION_MODE)


@st.cache_data(show_spinner=False)
def load_synthetic_transactions():
    return load_transactions()


def run_analysis(transactions):
    """Run the existing analysis modules and return their structured outputs."""
    rules = analyze_accounts(transactions)
    anomalies = detect_account_anomalies(transactions)
    graph = analyze_transaction_graph(transactions)
    risk = aggregate_risk(rules, anomalies, graph)
    return {
        "transactions": transactions.copy(),
        "rules": rules,
        "anomalies": anomalies,
        "graph": graph,
        "risk": risk,
    }


def make_risk_distribution_figure(risk_results):
    distribution = risk_results["summary"]["risk_level_counts"]
    levels = ["LOW", "MEDIUM", "HIGH"]
    colors = {"LOW": "#2E8B72", "MEDIUM": "#E0A43A", "HIGH": "#C64B4B"}
    return px.bar(
        x=levels,
        y=[distribution[level] for level in levels],
        color=levels,
        color_discrete_map=colors,
        labels={"x": "Risk level", "y": "Accounts"},
        title="Account risk distribution",
    ).update_layout(showlegend=False, margin=dict(l=10, r=10, t=45, b=10))


def make_network_figure(transactions, risk_results, focus_account, node_limit=35):
    """Plot a bounded one-hop subgraph around the selected synthetic account."""
    graph = nx.DiGraph()
    successful = transactions.loc[transactions["status"].eq("SUCCESS")]
    for row in successful.itertuples(index=False):
        graph.add_edge(
            str(row.sender),
            str(row.receiver),
            transaction_count=graph.get_edge_data(
                str(row.sender), str(row.receiver), {}
            ).get("transaction_count", 0)
            + 1,
            amount_paise=int(row.amount_paise),
        )

    if focus_account not in graph:
        return go.Figure().update_layout(
            title="No successful transaction edges for this account"
        )

    neighbors = set(graph.predecessors(focus_account)) | set(
        graph.successors(focus_account)
    )
    chosen_neighbors = sorted(neighbors)[: max(0, node_limit - 1)]
    visible_nodes = set(chosen_neighbors) | {focus_account}
    visible_edges = [
        (source, target, data)
        for source, target, data in graph.edges(data=True)
        if source in visible_nodes and target in visible_nodes
    ][:80]

    view = nx.DiGraph()
    view.add_nodes_from(visible_nodes)
    view.add_edges_from((source, target) for source, target, _ in visible_edges)
    positions = nx.spring_layout(view, seed=42)
    risk_by_account = {
        account["account_id"]: account
        for account in risk_results["accounts"]
    }

    edge_x = []
    edge_y = []
    annotations = []
    for source, target, _ in visible_edges:
        source_x, source_y = positions[source]
        target_x, target_y = positions[target]
        edge_x.extend((source_x, target_x, None))
        edge_y.extend((source_y, target_y, None))
        annotations.append(
            {
                "x": target_x,
                "y": target_y,
                "ax": source_x,
                "ay": source_y,
                "xref": "x",
                "yref": "y",
                "axref": "x",
                "ayref": "y",
                "showarrow": True,
                "arrowhead": 2,
                "arrowsize": 0.8,
                "arrowwidth": 1,
                "arrowcolor": "#9AA6B2",
                "opacity": 0.65,
            }
        )

    node_x = []
    node_y = []
    node_text = []
    node_colors = []
    for node in sorted(visible_nodes):
        account = risk_by_account.get(node, {})
        node_x.append(positions[node][0])
        node_y.append(positions[node][1])
        node_text.append(
            f"{node}<br>Risk: {account.get('risk_score', 0):.1f} "
            f"({account.get('risk_level', 'LOW')})"
        )
        node_colors.append(
            {
                "LOW": "#2E8B72",
                "MEDIUM": "#E0A43A",
                "HIGH": "#C64B4B",
            }.get(account.get("risk_level", "LOW"), "#6683A3")
        )

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=edge_x,
            y=edge_y,
            mode="lines",
            line=dict(width=1, color="#AAB4BE"),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=node_x,
            y=node_y,
            mode="markers+text",
            text=[node.rsplit("-", 1)[-1] for node in sorted(visible_nodes)],
            textposition="top center",
            hovertext=node_text,
            hoverinfo="text",
            marker=dict(
                size=[22 if node == focus_account else 14 for node in sorted(visible_nodes)],
                color=node_colors,
                line=dict(width=1, color="white"),
            ),
            showlegend=False,
        )
    )
    figure.update_layout(
        title=f"Successful transaction network around {focus_account}",
        annotations=annotations,
        height=520,
        margin=dict(l=10, r=10, t=50, b=10),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        plot_bgcolor="white",
    )
    return figure


def render_account_activity(ui, activity):
    ui.subheader("Account activity timeline")
    if not activity:
        ui.info("No payment activity was found for this account.")
        return
    ui.dataframe(
        [
            {
                "Direction": item.direction.value,
                "Payment ID": item.payment_id,
                "Counterparty": item.counterparty_account_id,
                "Amount (paise)": item.amount_paise,
                "Currency": item.currency,
                "Payment status": item.payment_status.value,
                "Created at": item.created_at,
                "Risk status": item.risk_status.value,
                "Data source": item.data_source.value,
            }
            for item in activity
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_payment_details(ui, payment):
    ui.subheader(f"Payment details — {payment.payment_id}")
    ui.caption(f"Data source: {payment.data_source.value}")
    ui.json(payment.model_dump(mode="json"))


def render_detection_result(ui, result):
    ui.subheader(f"Detection result — {result.detection_result_id}")
    ui.caption(f"Data source: {result.data_source.value}")
    ui.json(result.model_dump(mode="json"))


def render_investigation_reviews(ui, client, payment_id, detection_results):
    ui.subheader("Investigator reviews")
    ui.info(
        "Reviews are simulated/investigative records only. They do not change "
        "payment status or risk status and do not trigger financial actions."
    )
    reviews = client.list_payment_reviews(payment_id)
    if reviews:
        ui.dataframe(
            [
                {
                    "Reviewer": review.reviewer_id,
                    "Decision": review.decision.value,
                    "Notes": review.note or "",
                    "Timestamp": review.created_at,
                }
                for review in reviews
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        ui.info("No investigator reviews have been recorded for this payment.")

    if not detection_results:
        ui.caption("A detection result is required to associate a review.")
        return

    ui.markdown("**Add an investigative review**")
    detection_ids = [item.detection_result_id for item in detection_results]
    detection_result_id = ui.selectbox(
        "Review evidence",
        detection_ids,
        key="investigation_review_detection_id",
    )
    reviewer_id = ui.text_input(
        "Reviewer ID",
        placeholder="USER-...",
        key="investigation_reviewer_id",
    ).strip()
    decision = ui.selectbox(
        "Review decision",
        list(HumanReviewDecision),
        format_func=lambda item: item.value.replace("_", " ").title(),
        key="investigation_review_decision",
    )
    note = ui.text_area(
        "Investigation notes (optional)",
        max_chars=4000,
        key="investigation_review_note",
    )
    if ui.button("Submit investigative review", key="submit_investigation_review"):
        try:
            if not reviewer_id:
                raise ValueError("Enter a valid reviewer ID (USER-...).")
            with ui.spinner("Saving append-only investigative review..."):
                client.create_payment_review(
                    payment_id,
                    detection_result_id,
                    reviewer_id,
                    decision,
                    note,
                )
                reviews = client.list_payment_reviews(payment_id)
            ui.success("Investigative review recorded.")
            if reviews:
                ui.dataframe(
                    [
                        {
                            "Reviewer": review.reviewer_id,
                            "Decision": review.decision.value,
                            "Notes": review.note or "",
                            "Timestamp": review.created_at,
                        }
                        for review in reviews
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
        except (ValueError, InvestigationAPIError) as error:
            ui.error(str(error))


def render_investigation_mode(ui=st, client=None):
    """Render the explicitly selected, read-only payment investigation view."""
    ui.header("Payment Service / Investigation")
    ui.info(
        "Source: STREAMING_PAYMENT_SERVICE. This view is read-only and does not "
        "run detection or change payment state."
    )
    account_id = ui.text_input(
        "Account ID",
        placeholder="ACC-...",
        key="investigation_account_id",
    ).strip()
    if not account_id:
        ui.caption("Enter an account ID to load payment history and activity.")
        return

    owns_client = client is None
    try:
        client = client or InvestigationAPIClient()
        with ui.spinner("Loading account payments and activity..."):
            payments = client.list_account_payments(account_id)
            activity = client.list_account_activity(account_id)
        ui.subheader("Account payment history")
        if payments:
            ui.dataframe(
                [
                    {
                        "Payment ID": item.payment_id,
                        "Customer ID": item.customer_id,
                        "Merchant ID": item.merchant_id,
                        "Sender": item.sender_account_id,
                        "Receiver": item.receiver_account_id,
                        "Amount (paise)": item.amount_paise,
                        "Currency": item.currency,
                        "Status": item.status.value,
                        "Created at": item.created_at,
                        "Risk status": item.risk_status.value,
                        "Data source": item.data_source.value,
                    }
                    for item in payments
                ],
                use_container_width=True,
                hide_index=True,
            )
            payment_ids = [item.payment_id for item in payments]
            payment_id = ui.selectbox(
                "Select a payment",
                payment_ids,
                key="investigation_payment_id",
            )
            with ui.spinner("Loading payment investigation details..."):
                payment = client.get_payment(payment_id)
                detection_results = client.list_payment_detection_results(payment_id)
            render_payment_details(ui, payment)
            ui.subheader("Associated detection results")
            if detection_results:
                result_ids = [item.detection_result_id for item in detection_results]
                ui.dataframe(
                    [
                        {
                            "Detection result ID": item.detection_result_id,
                            "Protocol": item.protocol,
                            "Transaction ID": item.transaction_id,
                            "Risk level": item.risk_level,
                            "Risk score": item.risk_score,
                            "Created at": item.created_at,
                            "Data source": item.data_source.value,
                        }
                        for item in detection_results
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
                result_id = ui.selectbox(
                    "Select a detection result",
                    result_ids,
                    key="investigation_detection_result_id",
                )
                with ui.spinner("Loading detection result..."):
                    result = client.get_detection_result(result_id)
                render_detection_result(ui, result)
            else:
                ui.info("No detection results are associated with this payment.")
            render_investigation_reviews(
                ui,
                client,
                payment_id,
                detection_results,
            )
        else:
            ui.info("No payments were found for this account.")
        render_account_activity(ui, activity)
    except ValueError as error:
        ui.error(str(error))
    except InvestigationAPIError as error:
        ui.error(str(error))
    finally:
        if owns_client and client is not None:
            client.close()


def main():
    st.set_page_config(
        page_title="MuleGuard AI",
        page_icon="🛡️",
        layout="wide",
    )
    st.title("MuleGuard AI")
    st.caption("Synthetic transaction network analysis")
    st.warning(
        "Synthetic research data only — this dashboard is not connected to banks, "
        "UPI, or payment gateways. Alerts are review indicators, not proof of "
        "criminal activity. No account actions are performed."
    )

    mode = st.radio(
        "Data/source mode",
        DATA_SOURCE_MODES,
        index=0,
        key="dashboard_data_source_mode",
    )
    if mode == INVESTIGATION_MODE:
        render_investigation_mode()
        st.caption(DISCLAIMER)
        return

    st.caption("Data source: OFFLINE_DATASET")
    try:
        transactions = load_synthetic_transactions()
    except (FileNotFoundError, ValueError) as error:
        st.error(f"Could not load the synthetic transaction dataset: {error}")
        st.stop()

    if "analysis" not in st.session_state:
        with st.spinner("Running analysis on the synthetic dataset..."):
            st.session_state.analysis = run_analysis(transactions)

    if st.button("Run analysis", type="primary"):
        with st.spinner("Re-running rule, anomaly, graph, and risk analysis..."):
            st.session_state.analysis = run_analysis(transactions)
        st.success("Analysis refreshed.")

    analysis = st.session_state.analysis
    risk_results = analysis["risk"]
    risk_accounts = risk_results["accounts"]
    alerts = [
        account
        for account in risk_accounts
        if account["risk_level"] in {"MEDIUM", "HIGH"}
    ]
    alerts.sort(key=lambda account: account["risk_score"], reverse=True)
    high_count = sum(account["risk_level"] == "HIGH" for account in risk_accounts)
    graph_summary = analysis["graph"]["summary"]

    st.header("1. Overview")
    metric_columns = st.columns(4)
    metric_columns[0].metric("Synthetic transactions", f"{len(transactions):,}")
    metric_columns[1].metric("Accounts analyzed", f"{len(risk_accounts):,}")
    metric_columns[2].metric("Review alerts", f"{len(alerts):,}")
    metric_columns[3].metric("High risk indicators", f"{high_count:,}")
    st.caption(
        f"Graph contains {graph_summary['successful_transaction_count']:,} "
        "successful synthetic transactions; declined transactions are excluded "
        "from graph edges."
    )

    left, right = st.columns(2)
    with left:
        st.header("2. Transaction table")
        statuses = ["All"] + sorted(transactions["status"].unique().tolist())
        selected_status = st.selectbox("Filter by status", statuses)
        shown_transactions = transactions
        if selected_status != "All":
            shown_transactions = transactions.loc[
                transactions["status"].eq(selected_status)
            ]
        st.dataframe(
            shown_transactions[
                [
                    "transaction_id",
                    "sender",
                    "receiver",
                    "amount_paise",
                    "currency",
                    "timestamp",
                    "status",
                    "scenario_label",
                ]
            ],
            use_container_width=True,
            hide_index=True,
            height=360,
        )
    with right:
        st.header("3. Risk distribution")
        st.plotly_chart(
            make_risk_distribution_figure(risk_results),
            use_container_width=True,
        )

    st.header("4. Suspicious account alerts")
    if alerts:
        alert_rows = [
            {
                "Account": account["account_id"],
                "Score": account["risk_score"],
                "Level": account["risk_level"],
                "Recommendation (simulated)": account["recommendation"],
                "Triggered methods": ", ".join(
                    name.replace("_", " ")
                    for name, item in account["method_contributions"].items()
                    if item["contribution"] > 0
                ),
            }
            for account in alerts
        ]
        st.dataframe(
            pd.DataFrame(alert_rows),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No accounts crossed the configured review thresholds.")

    account_ids = [account["account_id"] for account in risk_accounts]
    alert_ids = [account["account_id"] for account in alerts]
    st.header("5. Transaction network")
    focus_account = st.selectbox(
        "Select an account to explore",
        account_ids,
        index=account_ids.index(alert_ids[0]) if alert_ids else 0,
        key="network_account",
    )
    st.caption(
        "The graph shows only a bounded one-hop neighborhood of successful "
        "synthetic transactions. Node colors correspond to illustrative risk levels."
    )
    st.plotly_chart(
        make_network_figure(
            transactions,
            risk_results,
            focus_account,
        ),
        use_container_width=True,
    )

    st.header("6. Account-level explanation")
    details = next(
        account for account in risk_accounts if account["account_id"] == focus_account
    )
    st.subheader(
        f"{focus_account} — {details['risk_level']} "
        f"({details['risk_score']:.1f}/100)"
    )
    st.write(f"**Recommendation (simulated):** {details['recommendation']}")
    explanation_columns = st.columns(3)
    for column, method in zip(
        explanation_columns, ("rule_based", "ml_anomaly", "graph_based")
    ):
        contribution = details["method_contributions"][method]
        with column:
            st.markdown(f"**{method.replace('_', ' ').title()}**")
            st.metric("Score contribution", contribution["contribution"])
            st.caption(contribution["explanation"])
            for indicator in contribution["indicators"]:
                if isinstance(indicator, dict):
                    title = indicator.get("rule", "Indicator").replace("_", " ").title()
                    st.markdown(f"- **{title}:** {indicator.get('explanation', '')}")

    st.header("7. Synthetic scenario demonstration")
    st.write(
        "Run the analysis on the generated SYNTHETIC_SUSPICIOUS subset only. "
        "Its label is retained for display, not supplied as a model feature or rule."
    )
    if st.button("Run controlled synthetic scenario"):
        scenario = transactions.loc[
            transactions["scenario_label"].eq("SYNTHETIC_SUSPICIOUS")
        ].copy()
        if scenario.empty:
            st.error("The dataset contains no SYNTHETIC_SUSPICIOUS scenario rows.")
        else:
            with st.spinner("Analyzing controlled synthetic scenario..."):
                scenario_analysis = run_analysis(scenario)
            st.session_state.scenario_analysis = scenario_analysis
            st.success(
                f"Analyzed {len(scenario):,} synthetic scenario transactions."
            )

    scenario_analysis = st.session_state.get("scenario_analysis")
    if scenario_analysis:
        scenario_counts = scenario_analysis["risk"]["summary"]["risk_level_counts"]
        scenario_metrics = st.columns(3)
        scenario_metrics[0].metric(
            "Scenario transactions", len(scenario_analysis["transactions"])
        )
        scenario_metrics[1].metric(
            "Scenario accounts", scenario_analysis["risk"]["summary"]["account_count"]
        )
        scenario_metrics[2].metric(
            "Scenario HIGH indicators", scenario_counts["HIGH"]
        )
        st.dataframe(
            [
                account
                for account in scenario_analysis["risk"]["accounts"]
                if account["risk_level"] in {"MEDIUM", "HIGH"}
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.caption(DISCLAIMER)


if __name__ == "__main__":
    main()
