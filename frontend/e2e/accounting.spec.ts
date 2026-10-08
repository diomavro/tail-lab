import { expect, test } from '@playwright/test'
import { mockPutLabApi } from './fixtures/mock-api'

// README: "Every hedge result says which accounting produced it"
// (docs/adr/0027 §2). One test per surface that shows a put or hedge result,
// so a new surface without a label is the gap a reviewer looks for.

const STANDALONE = 'Accounting: standalone'

for (const tab of ['Workspace', 'Recommendations', 'Portfolio', 'Bake-off'] as const) {
  test(`${tab} names the standalone accounting`, async ({ page }) => {
    await mockPutLabApi(page)
    await page.goto('/')
    if (tab !== 'Workspace') await page.getByRole('tab', { name: tab }).click()
    const label = page.getByTestId('accounting')
    await expect(label).toHaveCount(1)
    await expect(label).toContainText(STANDALONE)
    await expect(label).toContainText('The Book tab judges hedges.')
  })
}

test('the Book names self-financed in lump sum and contributions, contribution-funded for the model', async ({
  page,
}) => {
  await mockPutLabApi(page)
  await page.goto('/')
  await page.getByRole('tab', { name: 'Book' }).click()
  await expect(page.getByTestId('accounting')).toContainText('Accounting: self-financed')
  await page.getByRole('radiogroup', { name: 'Plan' }).getByText('Monthly contributions').click()
  await expect(page.getByTestId('accounting')).toContainText('Accounting: self-financed')
  await page.getByRole('radiogroup', { name: 'Priced from' }).getByText('Our model').click()
  await expect(page.getByTestId('accounting')).toContainText('Accounting: contribution-funded')
})
