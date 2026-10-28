"""The billing module's public interface (ADR-0002).

**Price catalog** (#89; :mod:`~app.modules.billing.prices`): the catalog
reads and writes the ``app.workflows.price_catalog`` workflow calls
(:func:`create_service_price`, :func:`add_price_version`,
:func:`delete_price_version`, :func:`list_service_prices`,
:func:`get_service_price`, :func:`list_versions`, :func:`current_and_next`).

**For other modules:** :func:`lookup_price`
(:mod:`~app.modules.billing.pricing`), the catalog price in effect on a
local date (#91 session charges, #92 enrollment charges; #95 adds the
custom price above it).

**Ledger core** (#90). For other modules: :func:`ensure_billing_account`,
:func:`lock_billing_account`, :func:`post_charge`, :func:`post_adjustment`
(:mod:`~app.modules.billing.ledger`), :func:`account_balance`,
:func:`family_balance`, :func:`charge_balances`, :func:`ledger_entries`
and the pure :func:`charge_status` (:mod:`~app.modules.billing.balances`).
The billing account reads (#364) add :func:`account_of_family` and
:func:`charge_details` (a filtered page of charges, statuses in SQL).

- **Lock order (data-model §6):** the caller's own scheduling or courses
  locks first; the billing account row ``FOR UPDATE``
  (:func:`lock_billing_account`, which both posting primitives take) is
  always the **last** business lock. The App invariants are checked under
  it.
- :func:`post_charge` raises :class:`StudentHasNoFamilyError` (a plain
  exception) for a student without a family; #91 lists such an item as
  ``no_billing_account``. An already posted natural key returns
  ``created = False`` with nothing written: sweepers treat it as success.
- :func:`post_adjustment` raises :class:`ChargeNetNegativeError` (``409
  charge_net_negative``) for a credit below zero (I1) and releases the
  newest allocations above the new net (E5).
- Every primitive runs in the caller's Unit of Work and never commits; the
  reads take no lock and write nothing.
"""

from app.modules.billing.balances import account_balance as account_balance
from app.modules.billing.balances import account_credit as account_credit
from app.modules.billing.balances import account_of_family as account_of_family
from app.modules.billing.balances import charge_balances as charge_balances
from app.modules.billing.balances import charge_details as charge_details
from app.modules.billing.balances import charge_status as charge_status
from app.modules.billing.balances import family_balance as family_balance
from app.modules.billing.balances import ledger_entries as ledger_entries
from app.modules.billing.errors import ChargeNetNegativeError as ChargeNetNegativeError
from app.modules.billing.errors import (
    PriceCatalogValidationError as PriceCatalogValidationError,
)
from app.modules.billing.errors import (
    PriceEffectiveFromInvalidError as PriceEffectiveFromInvalidError,
)
from app.modules.billing.errors import (
    PriceVersionInEffectError as PriceVersionInEffectError,
)
from app.modules.billing.errors import PriceVersionInUseError as PriceVersionInUseError
from app.modules.billing.errors import (
    ServicePriceExistsError as ServicePriceExistsError,
)
from app.modules.billing.errors import (
    StudentHasNoFamilyError as StudentHasNoFamilyError,
)
from app.modules.billing.ledger import (
    ensure_billing_account as ensure_billing_account,
)
from app.modules.billing.ledger import lock_billing_account as lock_billing_account
from app.modules.billing.ledger import post_adjustment as post_adjustment
from app.modules.billing.ledger import post_charge as post_charge
from app.modules.billing.prices import CATALOG_MODELS as CATALOG_MODELS
from app.modules.billing.prices import CatalogItemRow as CatalogItemRow
from app.modules.billing.prices import CatalogKey as CatalogKey
from app.modules.billing.prices import CatalogNames as CatalogNames
from app.modules.billing.prices import CurrentAndNext as CurrentAndNext
from app.modules.billing.prices import add_price_version as add_price_version
from app.modules.billing.prices import create_service_price as create_service_price
from app.modules.billing.prices import current_and_next as current_and_next
from app.modules.billing.prices import current_and_next_of as current_and_next_of
from app.modules.billing.prices import delete_price_version as delete_price_version
from app.modules.billing.prices import get_service_price as get_service_price
from app.modules.billing.prices import list_service_prices as list_service_prices
from app.modules.billing.prices import list_versions as list_versions
from app.modules.billing.pricing import lookup_price as lookup_price
from app.modules.billing.schemas import AccountBalance as AccountBalance
from app.modules.billing.schemas import AdjustmentDraft as AdjustmentDraft
from app.modules.billing.schemas import (
    BillingAccountReference as BillingAccountReference,
)
from app.modules.billing.schemas import ChargeBalance as ChargeBalance
from app.modules.billing.schemas import ChargeDetail as ChargeDetail
from app.modules.billing.schemas import ChargeDetailPage as ChargeDetailPage
from app.modules.billing.schemas import ChargeDraft as ChargeDraft
from app.modules.billing.schemas import ChargeStatus as ChargeStatus
from app.modules.billing.schemas import LedgerEntry as LedgerEntry
from app.modules.billing.schemas import PostedAdjustment as PostedAdjustment
from app.modules.billing.schemas import PostedCharge as PostedCharge
from app.modules.billing.schemas import PriceInEffect as PriceInEffect
from app.modules.billing.schemas import StudentBalance as StudentBalance
