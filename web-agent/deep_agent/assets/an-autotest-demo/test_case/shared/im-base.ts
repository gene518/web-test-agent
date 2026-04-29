import { expect, type Locator, type Page } from '@playwright/test';

const LOGIN_URL = process.env.IM_LOGIN_URL ?? 'https://www.jk.cn/';
const GREETING_PATTERN = /智享家医|真人护航|AI医生|有问题尽管问我/;

type SendMessageOptions = {
  ensureMessageVisible?: boolean;
  timeout?: number;
};

// IMBaseFlow 只保留高频复用的方法。
// 当前收敛规则：同一个操作方法在 5 个以上用例中复用，才进入基础类。
export class IMBaseFlow {
  // 顶部导航区域，用于页面基础状态校验。
  readonly header: Locator;

  // 聊天输入框，所有文本问答场景都会复用。
  readonly inputBox: Locator;

  // 发送按钮固定使用 tap，避免移动端 touch 事件失效。
  readonly sendButton: Locator;

  // 右上角“新对话”按钮，低频场景直接在 spec 中操作该定位器。
  readonly newChatButton: Locator;

  // 欢迎话术节点，用于判断新会话是否初始化完成。
  readonly greeting: Locator;

  // 历史对话抽屉，低频历史管理场景直接在 spec 中组合使用。
  readonly historyDrawer: Locator;

  // 历史对话管理入口，保留为定位器，不单独封装成方法。
  readonly historyManageButton: Locator;

  constructor(private readonly page: Page) {
    this.header = page.getByRole('banner');
    this.inputBox = page.getByRole('textbox', { name: /有问题尽管问我|请输入|说点什么/ });
    this.sendButton = page.locator('span.chat-send-btn');
    this.newChatButton = page.locator('.header-right-icon.chat-icon');
    this.greeting = page.getByText(GREETING_PATTERN).first();
    this.historyDrawer = page.locator('.history-chat-drawer');
    this.historyManageButton = page.locator('.history-chat-title-right');
  }

  static async openNewConversation(page: Page): Promise<IMBaseFlow> {
    const flow = new IMBaseFlow(page);
    await flow.openImPage();
    await expect(flow.newChatButton).toBeVisible();

    // 默认使用 tap；如果业务存在弹窗覆盖，可在这里统一改造，而不是在各 spec 里复制前置逻辑。
    await flow.newChatButton.tap();
    await flow.assertConversationReady();

    // 应用在创建新会话后可能短暂打开历史抽屉，等待状态稳定。
    await page.waitForTimeout(3000);
    return flow;
  }

  // 打开 IM 页面并完成基础加载校验。
  // 注意：不在此处过度断言业务欢迎话术，因为页面可能恢复上次对话。
  private async openImPage(): Promise<void> {
    await this.page.goto(LOGIN_URL);
    await this.page.waitForTimeout(5000);
    await expect(this.page).toHaveTitle(/AI医生|健康|jk/i);
    await expect(this.inputBox).toBeVisible();
  }

  // 校验当前页面已回到新会话初始态。
  private async assertConversationReady(): Promise<void> {
    await expect(this.greeting).toBeVisible();
    await expect(this.inputBox).toBeVisible();
  }

  // 高频操作：输入消息并通过发送按钮发送。
  async sendMessage(message: string, options: SendMessageOptions = {}): Promise<void> {
    const { ensureMessageVisible = true, timeout = 30000 } = options;

    await this.inputBox.tap();
    await this.inputBox.fill(message);
    await this.sendButton.waitFor({ state: 'visible', timeout });
    await this.sendButton.tap();

    if (ensureMessageVisible) {
      await expect(this.page.getByText(message, { exact: true })).toBeVisible({ timeout });
    }
  }
}
