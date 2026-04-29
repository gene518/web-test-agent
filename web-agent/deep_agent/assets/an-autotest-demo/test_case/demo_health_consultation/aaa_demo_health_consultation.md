# 健康咨询 Demo 功能测试计划

## Application Overview

本计划用于演示北大医疗 AI 医生 IM 应用的基础自动化写法，重点覆盖「新对话初始化」「文本咨询发送」「档案 Tab 切换」三个可复用路径。该目录为已实现计划目录，因此不带 `aaaplanning_` 前缀，并包含对应 `.spec.ts` 文件。

## Test Scenarios

### 1. 基础健康咨询流程
**Seed:** `test_case/specs/seed.spec.ts`

#### 1.1. a_发送健康咨询消息
**File:** `test_case/demo_health_consultation/a_send_text_message.spec.ts`

**Steps:**
  1. 访问登录并跳转到IM页面
     - expect: 登录成功并自动跳转到IM页面
     - expect: 页面成功加载并显示AI医生标题
  2. 点击右上角开启新对话图标
     - expect: 显示欢迎话术
  3. 在输入框输入「最近总是睡不好怎么办」并点击发送按钮
     - expect: 用户发送的文本消息在对话区可见
  4. 等待 AI 回复
     - expect: 对话区出现睡眠、建议、医生或健康相关回复

### 2. 档案入口流程
**Seed:** `test_case/specs/seed.spec.ts`

#### 2.1. b_档案tab切换加载正常
**File:** `test_case/demo_health_consultation/b_profile_tab_load.spec.ts`

**Steps:**
  1. 访问登录并跳转到IM页面
     - expect: 登录成功并自动跳转到IM页面
     - expect: 页面成功加载并显示AI医生标题
  2. 点击右上角开启新对话图标
     - expect: 显示欢迎话术
  3. 点击顶部「档案」Tab
     - expect: 「档案」Tab 被激活
     - expect: 档案页面内容区域可见
