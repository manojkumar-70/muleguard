"""Deterministic synthetic entities and payment specifications for Phase 7.2."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import random

from payment_service.repository import PaymentRepository
from payment_service.schemas import SyntheticCustomer, SyntheticMerchant


DEMO_SEED = 42
WARMUP_PAYMENT_COUNT = 50
FOCAL_ACCOUNT_ID = "ACC-M-000001"
BASE_TIMESTAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class DemoPaymentSpec:
    """Input to the existing SyntheticPaymentService, with stable ordering."""

    sequence: int
    customer_id: str
    merchant_id: str
    amount_paise: int
    created_at: datetime
    is_focal_payment: bool

    @property
    def payment_id(self) -> str:
        return f"PAY-{self.sequence:016x}"


@dataclass(frozen=True)
class DemoFixture:
    """Complete immutable data set used by the local demonstration."""

    seed: int
    customers: tuple[SyntheticCustomer, ...]
    merchants: tuple[SyntheticMerchant, ...]
    payments: tuple[DemoPaymentSpec, ...]


def build_demo_fixture() -> DemoFixture:
    """Build 50 warm-up payments followed by the ten fixed mule-pattern events."""
    customers = [
        _customer(f"CUST-DEMO-C-{index:02d}", f"ACC-DEMO-C-{index:02d}")
        for index in range(1, 6)
    ]
    merchants = [
        _merchant(f"MER-DEMO-M-{index:02d}", f"ACC-DEMO-M-{index:02d}")
        for index in range(1, 4)
    ]

    customers.extend(
        _customer(
            f"CUST-DEMO-S-{index:06d}",
            f"ACC-S-{index:06d}",
        )
        for index in range(1, 9)
    )
    customers.append(_customer("CUST-DEMO-MULE", FOCAL_ACCOUNT_ID))
    merchants.append(_merchant("MER-DEMO-MULE", FOCAL_ACCOUNT_ID))
    merchants.extend(
        _merchant(
            f"MER-DEMO-D-{index:06d}",
            f"ACC-D-{index:06d}",
        )
        for index in range(1, 3)
    )

    rng = random.Random(DEMO_SEED)
    payments = []
    for sequence in range(1, WARMUP_PAYMENT_COUNT + 1):
        customer_index = (sequence - 1) % 5
        merchant_index = (sequence - 1) % 3
        payments.append(
            DemoPaymentSpec(
                sequence=sequence,
                customer_id=f"CUST-DEMO-C-{customer_index + 1:02d}",
                merchant_id=f"MER-DEMO-M-{merchant_index + 1:02d}",
                amount_paise=10_000 + rng.randint(0, 5_000),
                created_at=BASE_TIMESTAMP + timedelta(minutes=sequence - 1),
                is_focal_payment=False,
            )
        )

    focal_events = [
        (
            f"CUST-DEMO-S-{index:06d}",
            "MER-DEMO-MULE",
            10_000 + index * 1_000,
        )
        for index in range(1, 9)
    ]
    focal_events.extend(
        [
            ("CUST-DEMO-MULE", "MER-DEMO-D-000001", 10_000),
            ("CUST-DEMO-MULE", "MER-DEMO-D-000002", 15_000),
        ]
    )
    for sequence, (customer_id, merchant_id, amount_paise) in enumerate(
        focal_events,
        start=WARMUP_PAYMENT_COUNT + 1,
    ):
        payments.append(
            DemoPaymentSpec(
                sequence=sequence,
                customer_id=customer_id,
                merchant_id=merchant_id,
                amount_paise=amount_paise,
                created_at=BASE_TIMESTAMP + timedelta(minutes=sequence - 1),
                is_focal_payment=True,
            )
        )

    return DemoFixture(
        seed=DEMO_SEED,
        customers=tuple(customers),
        merchants=tuple(merchants),
        payments=tuple(payments),
    )


def seed_demo_entities(
    repository: PaymentRepository,
    fixture: DemoFixture,
) -> None:
    """Insert the fixture's synthetic customer and merchant records."""
    for customer in fixture.customers:
        repository.save_customer(customer)
    for merchant in fixture.merchants:
        repository.save_merchant(merchant)


def _customer(customer_id: str, account_id: str) -> SyntheticCustomer:
    return SyntheticCustomer(
        customer_id=customer_id,
        display_name=f"Synthetic demo customer {customer_id}",
        account_id=account_id,
        created_at=BASE_TIMESTAMP,
    )


def _merchant(merchant_id: str, account_id: str) -> SyntheticMerchant:
    return SyntheticMerchant(
        merchant_id=merchant_id,
        display_name=f"Synthetic demo merchant {merchant_id}",
        account_id=account_id,
        created_at=BASE_TIMESTAMP,
    )
