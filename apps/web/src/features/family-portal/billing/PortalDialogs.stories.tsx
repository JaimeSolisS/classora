import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { CHARGE_PARTIAL } from '@/features/billing/accounts/fixtures';
import { i18n } from '@/i18n';
import { familyCharge, familyChargeDetail, familyPaymentDetail } from './api';
import { ChargeDetailDialog } from './ChargeDetailDialog';
import {
  ANA,
  CANARIES,
  DISCOUNTED_DETAIL,
  FAMILY_ID,
  LUIS,
  PACKAGE_ACTIVE,
  PAYMENT_DETAIL,
  PAYMENT_REFUNDED_DETAIL,
  PAYMENT_REVERSED_DETAIL,
  STAFF_PAYMENT_DETAIL,
  TODAY,
} from './fixtures';
import { PackageUsageDialog } from './PackageUsageDialog';
import { PaymentDetailDialog } from './PaymentDetailDialog';

// The portal's detail dialogs (#388 J2): a charge with discounts and
// adjustments, a payment (applied, reversed, refunded, and a staff-shaped
// one whose staff text never shows), a package's usage, and the phone
// sheet. No request; axe runs in light and dark.

const NAMES = { [ANA.id]: ANA.name, [LUIS.id]: LUIS.name };
const CHARGES = { [CHARGE_PARTIAL.id]: familyCharge(CHARGE_PARTIAL) };

function Dialogs({
  kind,
  payment = PAYMENT_DETAIL,
}: {
  kind: 'charge' | 'payment' | 'package';
  payment?: typeof PAYMENT_DETAIL;
}) {
  if (kind === 'charge') {
    return (
      <ChargeDetailDialog
        open
        state={{
          status: 'ready',
          value: familyChargeDetail(DISCOUNTED_DETAIL),
        }}
        onClose={fn()}
      />
    );
  }
  if (kind === 'package') {
    return (
      <PackageUsageDialog
        open
        state={{ status: 'ready', value: PACKAGE_ACTIVE }}
        today={TODAY}
        onClose={fn()}
      />
    );
  }
  return (
    <PaymentDetailDialog
      open
      familyId={FAMILY_ID}
      state={{
        status: 'ready',
        value: { payment: familyPaymentDetail(payment), charges: CHARGES },
      }}
      studentNames={NAMES}
      onClose={fn()}
    />
  );
}

const meta = {
  title: 'Family portal/Billing dialogs',
  component: Dialogs,
  args: { kind: 'charge' },
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
        tablet700: {
          name: 'Small tablet (700 px)',
          styles: { width: '700px', height: '800px' },
          type: 'tablet',
        },
      },
    },
  },
} satisfies Meta<typeof Dialogs>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ChargeDetail: Story = {
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('familyBilling.chargeDetail.discounts')),
    ).toBeVisible();
    await expect(dialog.textContent).not.toContain('CANARIO');
  },
};

export const PaymentApplied: Story = { args: { kind: 'payment' } };

export const PaymentReversed: Story = {
  args: { kind: 'payment', payment: PAYMENT_REVERSED_DETAIL },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('familyBilling.paymentDetail.reversal')),
    ).toBeVisible();
  },
};

export const PaymentRefunded: Story = {
  args: { kind: 'payment', payment: PAYMENT_REFUNDED_DETAIL },
};

export const StaffShapedPayment: Story = {
  args: { kind: 'payment', payment: STAFF_PAYMENT_DETAIL },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    for (const canary of CANARIES) {
      await expect(dialog.textContent).not.toContain(canary);
    }
  },
};

export const PackageUsage: Story = { args: { kind: 'package' } };

export const PhoneSheet: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(dialog.getBoundingClientRect().width).toBeLessThanOrEqual(320);
  },
};

/** #388 QA D2: still a full-width sheet between 640 and 767 px. */
export const TabletSheet: Story = {
  globals: { viewport: { value: 'tablet700' } },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    const width = document.documentElement.clientWidth;
    const box = dialog.getBoundingClientRect();
    if (width < 768) {
      await expect(Math.round(box.width)).toBe(width);
      await expect(Math.round(box.left)).toBe(0);
    } else {
      await expect(box.width).toBeLessThanOrEqual(672);
    }
  },
};
