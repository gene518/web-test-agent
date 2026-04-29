// spec: test_case/demo_health_consultation/aaa_demo_health_consultation.md
// seed: test_case/specs/seed.spec.ts

import { expect, test } from '../shared/base-test';
import { IMBaseFlow } from '../shared/im-base';

test.describe('档案入口流程', () => {
  test('b_档案tab切换加载正常', async ({ page }) => {
    // 1. 访问登录并跳转到IM页面
    // 2. 点击右上角开启新对话图标
    await IMBaseFlow.openNewConversation(page);

    // 3. 点击顶部「档案」Tab
    await page.getByText('档案', { exact: true }).tap();

    // expect: 「档案」Tab 被激活
    await expect(page.locator('.im-header-tabbar-item.active')).toContainText('档案');
    // expect: 档案页面内容区域可见
    await expect(page.locator('iframe')).toBeVisible();
  });
});
