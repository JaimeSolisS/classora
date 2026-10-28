"""The billing module's public interface (ADR-0002).

**Price catalog** (#89; :mod:`~app.modules.billing.prices`): the catalog
reads and writes the ``app.workflows.price_catalog`` workflow calls
(:func:`create_service_price`, :func:`add_price_version`,
:func:`delete_price_version`, :func:`list_service_prices`,
:func:`get_service_price`, :func:`list_versions`, :func:`current_and_next`).

**For other modules:** :func:`lookup_price`
(:mod:`~app.modules.billing.pricing`), the catalog price in effect on a
local date. Charges are priced through :func:`quote` (#95), never
:func:`lookup_price` directly.

**Price calculation** (#95; :mod:`~app.modules.billing.pricing`,
:mod:`~app.modules.billing.discounts`). For other modules:

- :func:`quote` (``PriceQuery`` → ``Quote | None``; ``None`` = no price):
  :func:`base_price` (a custom price of the student, then of the family,
  whose item is one of the #89 candidates; else exactly
  :func:`lookup_price`) then :func:`discounts_for` (at most one discount per
  kind, scholarship → sibling → discount → promotion, each on the running
  remainder). At most four statements, no lock. Post with
  ``ChargeDraft.priced(quote, …)``: :func:`post_charge` writes the
  ``charge_discounts`` snapshot only when it creates the charge. #92
  prorates between :func:`base_price` and :func:`discounts_for`.
- :func:`custom_prices_for` (the custom prices of many queries in one
  statement, for lists), :func:`apply_discount_chain` (given discounts in a
  given order: #99 reprices with a charge's snapshot) and
  :func:`charge_discount_lines` (the snapshot lines of charges, #96b/#102).

**Discounts and custom prices** (#95;
:mod:`~app.modules.billing.discount_config`,
:mod:`~app.modules.billing.custom_prices`): the reads and writes behind the
``app.workflows.billing_discounts`` and ``app.workflows.custom_prices``
routes.

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

**Billing settings and session charges** (#91;
:mod:`~app.modules.billing.settings`). For other modules:
:func:`get_billing_settings`, :func:`billing_starts_on` (the billing start
floor; ``lock=True`` reads the row ``FOR SHARE``, after the caller's
scheduling locks and before the account lock) and
:func:`package_covered_keys` (the package coverage seam, empty until #94;
the session sweeper and the unbilled list in ``app.workflows.
session_charges`` call it), and the unexecuted
:func:`session_charge_key_columns` (the posted session keys the sweeper
anti-joins). :func:`update_billing_settings` is the ``PATCH
/api/v1/billing/settings`` write.
"""

from app.modules.billing.balances import account_balance as account_balance
from app.modules.billing.balances import account_credit as account_credit
from app.modules.billing.balances import account_of_family as account_of_family
from app.modules.billing.balances import charge_balances as charge_balances
from app.modules.billing.balances import charge_details as charge_details
from app.modules.billing.balances import charge_status as charge_status
from app.modules.billing.balances import family_balance as family_balance
from app.modules.billing.balances import ledger_entries as ledger_entries
from app.modules.billing.custom_prices import (
    CUSTOM_PRICE_STATUSES as CUSTOM_PRICE_STATUSES,
)
from app.modules.billing.custom_prices import CustomPriceRow as CustomPriceRow
from app.modules.billing.custom_prices import (
    create_custom_price as create_custom_price,
)
from app.modules.billing.custom_prices import (
    delete_custom_price as delete_custom_price,
)
from app.modules.billing.custom_prices import get_custom_price as get_custom_price
from app.modules.billing.custom_prices import (
    list_custom_prices as list_custom_prices,
)
from app.modules.billing.custom_prices import (
    service_price_model as service_price_model,
)
from app.modules.billing.custom_prices import (
    update_custom_price as update_custom_price,
)
from app.modules.billing.discount_config import (
    DISCOUNT_STATUSES as DISCOUNT_STATUSES,
)
from app.modules.billing.discount_config import AssignmentRow as AssignmentRow
from app.modules.billing.discount_config import DiscountRow as DiscountRow
from app.modules.billing.discount_config import TargetNames as TargetNames
from app.modules.billing.discount_config import add_assignment as add_assignment
from app.modules.billing.discount_config import create_discount as create_discount
from app.modules.billing.discount_config import (
    delete_assignment as delete_assignment,
)
from app.modules.billing.discount_config import get_discount as get_discount
from app.modules.billing.discount_config import (
    list_assignments as list_assignments,
)
from app.modules.billing.discount_config import list_discounts as list_discounts
from app.modules.billing.discount_config import (
    lock_for_assignment as lock_for_assignment,
)
from app.modules.billing.discount_config import update_discount as update_discount
from app.modules.billing.discounts import KIND_ORDER as KIND_ORDER
from app.modules.billing.discounts import ChargeFacts as ChargeFacts
from app.modules.billing.discounts import (
    DiscountEligibility as DiscountEligibility,
)
from app.modules.billing.discounts import DiscountLine as DiscountLine
from app.modules.billing.discounts import DiscountSpec as DiscountSpec
from app.modules.billing.discounts import (
    apply_discount_chain as apply_discount_chain,
)
from app.modules.billing.discounts import matches as matches
from app.modules.billing.discounts import select_discounts as select_discounts
from app.modules.billing.errors import (
    AudienceNotAssignedError as AudienceNotAssignedError,
)
from app.modules.billing.errors import (
    BillingValidationError as BillingValidationError,
)
from app.modules.billing.errors import ChargeNetNegativeError as ChargeNetNegativeError
from app.modules.billing.errors import (
    CustomPriceEffectiveFromInvalidError as CustomPriceEffectiveFromInvalidError,
)
from app.modules.billing.errors import (
    CustomPriceExpiryInvalidError as CustomPriceExpiryInvalidError,
)
from app.modules.billing.errors import (
    CustomPriceInEffectError as CustomPriceInEffectError,
)
from app.modules.billing.errors import CustomPriceInUseError as CustomPriceInUseError
from app.modules.billing.errors import (
    CustomPriceOverlapError as CustomPriceOverlapError,
)
from app.modules.billing.errors import (
    DiscountAssignmentExistsError as DiscountAssignmentExistsError,
)
from app.modules.billing.errors import (
    DiscountNameExistsError as DiscountNameExistsError,
)
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
    charge_discount_lines as charge_discount_lines,
)
from app.modules.billing.ledger import (
    ensure_billing_account as ensure_billing_account,
)
from app.modules.billing.ledger import lock_billing_account as lock_billing_account
from app.modules.billing.ledger import post_adjustment as post_adjustment
from app.modules.billing.ledger import post_charge as post_charge
from app.modules.billing.ledger import (
    session_charge_key_columns as session_charge_key_columns,
)
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
from app.modules.billing.pricing import base_price as base_price
from app.modules.billing.pricing import custom_prices_for as custom_prices_for
from app.modules.billing.pricing import discounts_for as discounts_for
from app.modules.billing.pricing import lookup_price as lookup_price
from app.modules.billing.pricing import make_quote as make_quote
from app.modules.billing.pricing import quote as quote
from app.modules.billing.schemas import AccountBalance as AccountBalance
from app.modules.billing.schemas import AdjustmentDraft as AdjustmentDraft
from app.modules.billing.schemas import BasePrice as BasePrice
from app.modules.billing.schemas import (
    BillingAccountReference as BillingAccountReference,
)
from app.modules.billing.schemas import (
    BillingSettingsDetail as BillingSettingsDetail,
)
from app.modules.billing.schemas import (
    BillingSettingsUpdate as BillingSettingsUpdate,
)
from app.modules.billing.schemas import ChargeBalance as ChargeBalance
from app.modules.billing.schemas import ChargeDetail as ChargeDetail
from app.modules.billing.schemas import ChargeDetailPage as ChargeDetailPage
from app.modules.billing.schemas import ChargeDraft as ChargeDraft
from app.modules.billing.schemas import ChargeStatus as ChargeStatus
from app.modules.billing.schemas import CoverageQuery as CoverageQuery
from app.modules.billing.schemas import CustomPriceCreate as CustomPriceCreate
from app.modules.billing.schemas import CustomPriceUpdate as CustomPriceUpdate
from app.modules.billing.schemas import (
    DiscountAssignmentCreate as DiscountAssignmentCreate,
)
from app.modules.billing.schemas import DiscountCreate as DiscountCreate
from app.modules.billing.schemas import DiscountUpdate as DiscountUpdate
from app.modules.billing.schemas import LedgerEntry as LedgerEntry
from app.modules.billing.schemas import PostedAdjustment as PostedAdjustment
from app.modules.billing.schemas import PostedCharge as PostedCharge
from app.modules.billing.schemas import PriceInEffect as PriceInEffect
from app.modules.billing.schemas import PriceQuery as PriceQuery
from app.modules.billing.schemas import Quote as Quote
from app.modules.billing.schemas import StudentBalance as StudentBalance
from app.modules.billing.settings import billing_starts_on as billing_starts_on
from app.modules.billing.settings import (
    get_billing_settings as get_billing_settings,
)
from app.modules.billing.settings import (
    package_covered_keys as package_covered_keys,
)
from app.modules.billing.settings import (
    update_billing_settings as update_billing_settings,
)

ChargeDiscountLine = DiscountLine
"""A charge's snapshot line (:func:`charge_discount_lines`, #95 B6)."""
