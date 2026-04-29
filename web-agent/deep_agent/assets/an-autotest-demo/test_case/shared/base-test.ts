import { test as base } from '@playwright/test';
import { IMBaseFlow } from './im-base';

// 扩展默认 test：用例结束后等待 3 秒并尝试重置聊天状态，方便结果校验。
// 注意：cleanup 失败不影响测试结果，但测试主体失败必须继续抛出，不能被吞掉。
export const test = base.extend({
  page: async ({ page }, use) => {
    await use(page);

    try {
      await page.waitForTimeout(3000);
      await IMBaseFlow.openNewConversation(page);
    } catch {
      // 清理失败不影响测试结果，忽略异常。
    }
  },
});

export { expect } from '@playwright/test';
