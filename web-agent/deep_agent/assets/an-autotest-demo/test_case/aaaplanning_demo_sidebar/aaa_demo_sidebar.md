# 侧边栏 Demo 功能测试计划

## Application Overview

本计划用于演示未实现计划目录的命名规范。当前目录带 `aaaplanning_` 前缀，表示仅有测试计划文档、尚未生成 `.spec.ts` 脚本。生成第一个脚本后，应将目录重命名为 `test_case/demo_sidebar/`，并保留 `aaa_demo_sidebar.md` 在目录首位。

## Test Scenarios

### 1. 侧边栏展开与展示验证
**Seed:** `test_case/specs/seed.spec.ts`

#### 1.1. a_侧边栏展开后各区域元素正常显示
**File:** `test_case/demo_sidebar/a_sidebar_open_display.spec.ts`

**Steps:**
  1. 访问登录并跳转到IM页面
     - expect: 登录成功并自动跳转到IM页面
     - expect: 页面成功加载并显示AI医生标题
  2. 点击右上角开启新对话图标
     - expect: 显示欢迎话术
  3. 点击右上角历史/侧边栏图标
     - expect: 侧边栏抽屉从右侧滑入并变为可见
  4. 查看侧边栏头部区域
     - expect: 显示编辑入口、用户昵称、客服入口
  5. 查看功能入口区域
     - expect: 显示我的订单、我的权益、优惠券、平安芯医等入口

#### 1.2. b_点击遮罩关闭侧边栏
**File:** `test_case/demo_sidebar/b_sidebar_close_overlay.spec.ts`

**Steps:**
  1. 访问登录并跳转到IM页面
     - expect: 登录成功并自动跳转到IM页面
     - expect: 页面成功加载并显示AI医生标题
  2. 点击右上角开启新对话图标
     - expect: 显示欢迎话术
  3. 点击右上角历史/侧边栏图标
     - expect: 侧边栏可见
  4. 点击遮罩层
     - expect: 侧边栏关闭
     - expect: 回到 IM 对话页面正常状态
