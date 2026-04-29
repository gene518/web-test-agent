// spec: test_case/demo_health_consultation/aaa_demo_health_consultation.md
// seed: test_case/specs/seed.spec.ts

import { expect, test } from '../shared/base-test';
import { IMBaseFlow } from '../shared/im-base';

test.describe('基础健康咨询流程', () => {
  test('a_发送健康咨询消息', async ({ page }) => {
    // 1. 访问登录并跳转到IM页面
    // 2. 点击右上角开启新对话图标
    const im = await IMBaseFlow.openNewConversation(page);

    // 3. 在输入框输入「最近总是睡不好怎么办」并点击发送按钮
    await im.sendMessage('最近总是睡不好怎么办');

    // expect: 用户发送的文本消息在对话区可见
    await expect(page.getByText('最近总是睡不好怎么办', { exact: true })).toBeVisible();

    // 4. 等待 AI 回复
    // expect: 对话区出现睡眠、建议、医生或健康相关回复
    await expect(page.getByText(/睡眠|建议|医生|健康/).last()).toBeVisible({ timeout: 60000 });
  });
});
