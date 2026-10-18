// The local demo Owner (`identity.demo_owner` demo generator, ADR-0007 D12):
// development placeholders that exist only in local stacks (APP_ENV=local),
// including the E2E stack. Same values as DEMO_OWNER_USERNAME and
// DEMO_OWNER_PASSWORD in apps/api/tests/factories/identity.py.
export const demoOwner = {
  username: 'demo.owner',
  password: 'classora-demo-owner',
} as const;
