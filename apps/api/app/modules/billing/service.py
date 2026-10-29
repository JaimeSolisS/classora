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

**Enrollment charges** (#92; ``app.workflows.enrollment_charges``). For
other modules: ``quote(query, prorate=(remaining, total))`` (the base price
prorated **before** the discounts, J3); ``ChargeDraft.proration`` (the
snapshot of the counts on a ``group_enrollment``/``cohort_enrollment``
charge, J8); the unexecuted :func:`enrollment_fee_columns` (the enrollment
fees with their net: the sweeper's anti-join and the J5 re-enrollment rule)
and :func:`group_fee_item_columns` (the ``group/full_course`` items, D3);
and :func:`session_charge_states` (session charges with their net and their
Σ ``enrollment_removal``, for the J4 removal credits and restorations).

**Tuition plans** (#93; :mod:`~app.modules.billing.tuition` (the pure
period grid and :func:`plan_period_state`) and
:mod:`~app.modules.billing.tuition_plans` (the plan and pause rows); the
workflows ``app.workflows.tuition_plans`` and
``app.workflows.tuition_charges``). For other modules:
``PriceQuery.service_price_id`` (the pin: :func:`quote` prices exactly that
item, its custom prices then its version in effect; required for
``per_period``, which :func:`lookup_price` refuses), ``ChargeDraft.
tuition_plan_id`` and ``service_period_end`` (a ``tuition_period``
charge), :func:`pinned_base_prices` (many pinned base prices in two
statements), and the unexecuted :func:`linked_plan_columns` with
:func:`linked_plan_windows` (the K6 ``tuition_priced`` outcome of the #91
and #92 classifications).
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
    StudentHasNoFamilyAccountError as StudentHasNoFamilyAccountError,
)
from app.modules.billing.errors import (
    StudentHasNoFamilyError as StudentHasNoFamilyError,
)
from app.modules.billing.errors import (
    TuitionPauseInvalidError as TuitionPauseInvalidError,
)
from app.modules.billing.errors import (
    TuitionPauseNotOpenError as TuitionPauseNotOpenError,
)
from app.modules.billing.errors import (
    TuitionPauseOverlapError as TuitionPauseOverlapError,
)
from app.modules.billing.errors import TuitionPlanEndedError as TuitionPlanEndedError
from app.modules.billing.errors import (
    TuitionPlanEndsOnInvalidError as TuitionPlanEndsOnInvalidError,
)
from app.modules.billing.errors import (
    TuitionPlanEnrollmentFeeChargedError as TuitionPlanEnrollmentFeeChargedError,
)
from app.modules.billing.errors import (
    TuitionPlanEnrollmentInvalidError as TuitionPlanEnrollmentInvalidError,
)
from app.modules.billing.errors import (
    TuitionPlanHasChargesError as TuitionPlanHasChargesError,
)
from app.modules.billing.errors import (
    TuitionPlanOverlapError as TuitionPlanOverlapError,
)
from app.modules.billing.errors import (
    TuitionPlanPriceInvalidError as TuitionPlanPriceInvalidError,
)
from app.modules.billing.errors import (
    TuitionPlanSessionsChargedError as TuitionPlanSessionsChargedError,
)
from app.modules.billing.errors import (
    TuitionPlanStartsOnInvalidError as TuitionPlanStartsOnInvalidError,
)
from app.modules.billing.errors import (
    TuitionResumeInvalidError as TuitionResumeInvalidError,
)
from app.modules.billing.ledger import (
    ENROLLMENT_REMOVAL as ENROLLMENT_REMOVAL,
)
from app.modules.billing.ledger import SessionChargeState as SessionChargeState
from app.modules.billing.ledger import (
    charge_discount_lines as charge_discount_lines,
)
from app.modules.billing.ledger import (
    enrollment_fee_columns as enrollment_fee_columns,
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
from app.modules.billing.ledger import (
    session_charge_states as session_charge_states,
)
from app.modules.billing.prices import CATALOG_MODELS as CATALOG_MODELS
from app.modules.billing.prices import CUSTOM_PRICE_MODELS as CUSTOM_PRICE_MODELS
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
from app.modules.billing.prices import get_service_prices as get_service_prices
from app.modules.billing.prices import list_service_prices as list_service_prices
from app.modules.billing.prices import list_versions as list_versions
from app.modules.billing.pricing import base_price as base_price
from app.modules.billing.pricing import custom_prices_for as custom_prices_for
from app.modules.billing.pricing import discounts_for as discounts_for
from app.modules.billing.pricing import (
    group_fee_item_columns as group_fee_item_columns,
)
from app.modules.billing.pricing import lookup_price as lookup_price
from app.modules.billing.pricing import make_quote as make_quote
from app.modules.billing.pricing import pinned_base_prices as pinned_base_prices
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
from app.modules.billing.schemas import PeriodFrequency as PeriodFrequency
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
from app.modules.billing.tuition import PERIOD_STATES as PERIOD_STATES
from app.modules.billing.tuition import Frequency as Frequency
from app.modules.billing.tuition import Period as Period
from app.modules.billing.tuition import PeriodState as PeriodState
from app.modules.billing.tuition import PlanTerms as PlanTerms
from app.modules.billing.tuition import in_pause as in_pause
from app.modules.billing.tuition import is_period_end as is_period_end
from app.modules.billing.tuition import is_period_start as is_period_start
from app.modules.billing.tuition import period_at as period_at
from app.modules.billing.tuition import period_containing as period_containing
from app.modules.billing.tuition import period_index as period_index
from app.modules.billing.tuition import period_start as period_start
from app.modules.billing.tuition import periods_between as periods_between
from app.modules.billing.tuition import plan_period_state as plan_period_state
from app.modules.billing.tuition_plans import CANDIDATE_PAGE as CANDIDATE_PAGE
from app.modules.billing.tuition_plans import PauseRow as PauseRow
from app.modules.billing.tuition_plans import PerPeriodItem as PerPeriodItem
from app.modules.billing.tuition_plans import PostedPeriod as PostedPeriod
from app.modules.billing.tuition_plans import TuitionPlanRow as TuitionPlanRow
from app.modules.billing.tuition_plans import add_pause as add_pause
from app.modules.billing.tuition_plans import create_plan as create_plan
from app.modules.billing.tuition_plans import delete_plan as delete_plan
from app.modules.billing.tuition_plans import end_pause as end_pause
from app.modules.billing.tuition_plans import get_plan as get_plan
from app.modules.billing.tuition_plans import (
    group_fee_enrollment_ids as group_fee_enrollment_ids,
)
from app.modules.billing.tuition_plans import group_fee_net as group_fee_net
from app.modules.billing.tuition_plans import linked_plan_columns as linked_plan_columns
from app.modules.billing.tuition_plans import linked_plan_windows as linked_plan_windows
from app.modules.billing.tuition_plans import list_plans as list_plans
from app.modules.billing.tuition_plans import per_period_item as per_period_item
from app.modules.billing.tuition_plans import per_period_items as per_period_items
from app.modules.billing.tuition_plans import plan_has_charges as plan_has_charges
from app.modules.billing.tuition_plans import plan_ids_of as plan_ids_of
from app.modules.billing.tuition_plans import plan_overlaps as plan_overlaps
from app.modules.billing.tuition_plans import plan_pauses as plan_pauses
from app.modules.billing.tuition_plans import (
    positive_session_charges as positive_session_charges,
)
from app.modules.billing.tuition_plans import (
    posted_period_starts as posted_period_starts,
)
from app.modules.billing.tuition_plans import posted_through as posted_through
from app.modules.billing.tuition_plans import recent_periods as recent_periods
from app.modules.billing.tuition_plans import tuition_candidates as tuition_candidates
from app.modules.billing.tuition_plans import update_plan as update_plan

ChargeDiscountLine = DiscountLine
"""A charge's snapshot line (:func:`charge_discount_lines`, #95 B6)."""
