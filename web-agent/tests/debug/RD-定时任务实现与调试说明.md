# 定时任务实现与调试说明

## 1. 目标与边界

定时任务能力用于把已经生成好的 Playwright 自动化脚本按 Cron 时间表达式周期执行。

当前实现分为两条链路：

1. 配置更新链路：用户在前端对话里提出“把某个定时任务改成几点执行、启用/禁用、改成有头/无头、改执行哪些脚本”，Master 识别为 `scheduler` 意图后，调用 `SchedulerAgent` 修改配置文件。
2. 独立执行链路：`web-agent-scheduler` 命令启动一个常驻轮询服务，定期读取 JSON 配置文件，判断哪些任务到点，然后串行执行 `npx playwright test`。

注意：当前 Scheduler Agent 只支持更新已存在的任务，不负责创建新任务，也不负责直接执行测试。新任务需要先写入配置文件，再由调度服务扫描执行。

## 2. 总体流程

```text
用户对话
  -> Master Intent 判断为 scheduler
  -> 提取 project_name/project_dir、schedule_task_id、schedule_cron 等参数
  -> SchedulerAgent.update_existing_task_config()
  -> 写回 scheduler_tasks.json

独立调度服务
  -> web-agent-scheduler 启动
  -> 每隔 poll_interval_seconds 读取 scheduler_tasks.json
  -> Pydantic 校验配置
  -> CronExpression 判断当前分钟是否命中
  -> 生成 PendingScheduledRun
  -> 串行队列执行 PlaywrightTaskRunner
  -> 在项目 test_root_dir 写 scheduler-service.log
```

## 3. 如何启动调度服务

推荐从仓库根目录运行：

```bash
uv run --project web-agent web-agent-scheduler
```

指定配置文件：

```bash
uv run --project web-agent web-agent-scheduler --config ~/webautotest/scheduler_tasks.json
```

也可以先进入后端目录：

```bash
cd web-agent
uv run web-agent-scheduler --config ~/webautotest/scheduler_tasks.json
```

默认配置文件路径来自环境变量解析：

```text
SCHEDULER_CONFIG_PATH 非空时：使用该路径
SCHEDULER_CONFIG_PATH 为空时：DEFAULT_AUTOMATION_PROJECT_ROOT/scheduler_tasks.json
```

默认 `DEFAULT_AUTOMATION_PROJECT_ROOT` 是 `~/webautotest`，所以默认配置文件是：

```text
~/webautotest/scheduler_tasks.json
```

## 4. 如何配置定时任务

可参考 [web-agent/scheduler_tasks.example.json](/Users/jin/Documents/code/github/web-test-agent/web-agent/scheduler_tasks.example.json)：

```json
{
  "scheduler": {
    "poll_interval_seconds": 30
  },
  "projects": [
    {
      "project_name": "demo",
      "test_root_dir": "test_case",
      "timezone": "Asia/Shanghai",
      "headed": false,
      "tasks": [
        {
          "task_id": "daily_smoke",
          "schedule": "0 9 * * *",
          "locations": [
            "test_case/demo_health_consultation/a_send_text_message.spec.ts"
          ],
          "enabled": true
        }
      ]
    }
  ]
}
```

字段说明：

- `scheduler.poll_interval_seconds`：轮询配置文件和检查到点任务的间隔，单位秒，最小值为 5。
- `projects[].project_name`：自动化项目名。未配置 `project_dir` 时，会解析为 `DEFAULT_AUTOMATION_PROJECT_ROOT/project_name`。
- `projects[].project_dir`：自动化项目目录。相对路径会相对 `DEFAULT_AUTOMATION_PROJECT_ROOT` 解析；绝对路径按原样解析。
- `projects[].test_root_dir`：测试根目录，默认是 `test_case`。调度日志会写到这个目录下。
- `projects[].timezone`：项目时区，例如 `Asia/Shanghai`。为空时使用服务进程本地时区。
- `projects[].headed`：项目默认浏览器模式，`true` 有头，`false` 无头。
- `projects[].tasks[]`：项目下的任务列表，`task_id` 在同一项目内必须唯一。
- `tasks[].task_id`：任务 ID。对话里的 Scheduler Agent 修改任务时靠它定位目标任务。
- `tasks[].schedule`：五段 Cron 表达式。
- `tasks[].locations`：传给 `npx playwright test` 的脚本或目录列表。为空数组时执行整个项目的 Playwright 测试。
- `tasks[].enabled`：是否启用任务。
- `tasks[].headed`：任务级浏览器模式覆盖。为空时继承项目级 `headed`。

## 5. Cron 表达式规则

当前只支持五段 Cron：

```text
minute hour day_of_month month day_of_week
```

支持的写法：

- `*`：任意值。
- `*/5`：每 5 个单位。
- `1,15,30`：枚举值。
- `1-10`：范围。
- `1-10/2`：范围加步长。

取值范围：

- minute：0-59
- hour：0-23
- day_of_month：1-31
- month：1-12
- day_of_week：0-7，0 和 7 都表示周日

当 `day_of_month` 和 `day_of_week` 同时不是 `*` 时，命中逻辑是 OR；只要日期或星期任一匹配即可。

## 6. 执行机制

调度服务每次 `poll_once()` 会做这些事：

1. 收割上一次已经完成的任务，写结束日志。
2. 重新读取配置文件。配置修改后不需要重启服务，下一轮扫描会生效。
3. 校验配置结构和 Cron 表达式。
4. 按项目时区计算当前分钟。
5. 找出命中当前分钟的启用任务。
6. 用 `(project_dir, task_id, scheduled_minute)` 去重，避免同一分钟重复入队。
7. 入队任务。如果已有任务正在执行或队列非空，会写“任务冲突”日志，但仍进入串行队列。
8. 空闲时启动下一个任务。

实际执行命令：

```bash
npx playwright test <locations...>
```

执行目录是任务对应的 `project_dir`。

调度服务会给 Playwright 进程注入这些环境变量：

- `PWTEST_HEADED`：`1` 表示有头，`0` 表示无头。
- `PW_TEST_REPORT_NAME`：形如 `scheduled-{task_id}-{YYYYMMDD-HHMM}`。
- `PW_SCHEDULE_TASK_ID`：任务 ID。
- `PW_SCHEDULE_PROJECT_NAME`：项目名。
- `PW_SCHEDULED_FOR`：计划执行时间，精确到分钟。

## 7. 日志位置与排查入口

每个项目的调度日志写在：

```text
{project_dir}/{test_root_dir}/scheduler-service.log
```

例如：

```text
~/webautotest/demo/test_case/scheduler-service.log
```

常见日志：

- `调度服务已加载项目`：配置文件中的项目已被服务识别。
- `任务命中执行窗口`：当前分钟命中 Cron。
- `任务冲突`：已有任务执行中或队列里已有任务，本任务被放入串行队列。
- `任务开始`：开始执行 `npx playwright test`。
- Playwright stdout：测试进程输出会按行写入同一个日志文件。
- `任务结束`：进程退出，包含 `exit_code` 和耗时。
- `任务执行失败`：`exit_code != 0`。
- `任务执行异常`：调度服务调用 runner 时抛异常。

## 8. 对话里如何修改定时任务

对话入口只做配置更新，示例：

```text
把 demo 项目的 daily_smoke 改成每天上午 10:15 执行
```

```text
把 daily_smoke 禁用
```

```text
把 demo 的 daily_smoke 改成无头执行，只跑 test_case/demo/a_case.spec.ts
```

Master 会尽量抽取这些字段：

- `project_name` 或 `project_dir`
- `schedule_task_id`
- `schedule_cron`
- `schedule_headed`
- `schedule_enabled`
- `schedule_locations`

必要参数：

- scheduler 意图至少需要 `project_name` 或 `project_dir`
- 必须有 `schedule_task_id`
- 必须至少有一个要更新的字段，例如 Cron、headed、enabled 或 locations

如果配置文件里不存在对应项目或任务，会返回错误，不会创建新条目。

## 9. 相关文件职责

- [web-agent/deep_agent/scheduler/cli.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/cli.py)：命令行入口，解析 `--config`，创建并启动 `SchedulerService`。
- [web-agent/deep_agent/scheduler/service.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/service.py)：调度执行核心。负责轮询配置、计算到点任务、串行排队、启动 Playwright、写项目日志。
- [web-agent/deep_agent/scheduler/models.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/models.py)：配置文件 Pydantic 模型。负责字段默认值、归一化、项目和任务唯一性校验、时区校验。
- [web-agent/deep_agent/scheduler/store.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/store.py)：配置文件读写和路径解析。负责加载 JSON、保存 JSON、解析项目目录、解析日志路径、更新已有任务。
- [web-agent/deep_agent/scheduler/cron.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/cron.py)：五段 Cron 解析和命中判断。
- [web-agent/deep_agent/agent/scheduler/scheduler_agent.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/agent/scheduler/scheduler_agent.py)：对话中的 Scheduler Agent，只负责根据 Master 提取参数更新已有配置。
- [web-agent/deep_agent/agent/master/models/intent.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/agent/master/models/intent.py)：声明 scheduler 意图字段和缺参规则。
- [web-agent/deep_agent/web_autotest_agent_workflow.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/web_autotest_agent_workflow.py)：主图把 `scheduler` 路由到 `scheduler_config_node`。
- [web-agent/scheduler_tasks.example.json](/Users/jin/Documents/code/github/web-test-agent/web-agent/scheduler_tasks.example.json)：配置文件示例。
- [web-agent/tests/test_scheduler_service.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/tests/test_scheduler_service.py)：调度配置更新、串行执行和日志行为测试。

## 10. 调试建议

### 10.1 先准备最小配置

```bash
mkdir -p ~/webautotest/demo/test_case
cp web-agent/scheduler_tasks.example.json ~/webautotest/scheduler_tasks.json
```

把配置里的 `locations` 改成当前项目里真实存在的 `.spec.ts` 文件。

### 10.2 让任务快速命中

把 `schedule` 改成当前分钟附近的表达式。例如当前时间是 14:23，可以临时改成：

```json
"schedule": "23 14 * * *"
```

或者每分钟执行：

```json
"schedule": "* * * * *"
```

调试每分钟执行时要注意串行队列可能堆积，调通后及时改回目标频率。

### 10.3 启动服务并观察日志

```bash
uv run --project web-agent web-agent-scheduler --config ~/webautotest/scheduler_tasks.json
```

另开终端：

```bash
tail -f ~/webautotest/demo/test_case/scheduler-service.log
```

### 10.4 单独验证 Playwright 命令

如果调度日志显示 Playwright 失败，先进入项目目录手动执行同一条命令：

```bash
cd ~/webautotest/demo
npx playwright test test_case/demo/a_case.spec.ts
```

如果手动也失败，优先修测试工程依赖、脚本路径、浏览器安装或用例本身。

### 10.5 检查配置能否被加载

可用 Python 直接校验配置：

```bash
uv run --project web-agent python - <<'PY'
from pathlib import Path
from deep_agent.scheduler.store import load_scheduler_config

config = load_scheduler_config(Path("~/webautotest/scheduler_tasks.json").expanduser())
print(config.model_dump(exclude_none=True))
PY
```

### 10.6 常见问题

- 配置文件不存在：确认 `SCHEDULER_CONFIG_PATH` 或默认 `~/webautotest/scheduler_tasks.json` 是否存在。
- 任务不执行：检查 `enabled` 是否为 `true`，Cron 是否命中当前项目时区。
- 找不到项目：检查 `project_name` 是否对应 `DEFAULT_AUTOMATION_PROJECT_ROOT/project_name`，或直接使用 `project_dir`。
- 找不到脚本：`locations` 是相对 `project_dir` 的路径，不能相对仓库根目录理解。
- 有任务冲突：当前实现是串行队列，同一时间多个任务都会排队执行。
- 对话修改失败：确认配置里已经有对应 `task_id`；当前不自动创建任务。

## 11. 当前实现限制

- 只支持五段 Cron，不支持秒级 Cron。
- 调度服务是单进程内存去重；重启服务后 `_last_scheduled_minutes` 会清空。
- 所有到点任务串行执行，没有并发执行策略。
- 队列没有最大长度限制；高频任务或长耗时任务可能堆积。
- Scheduler Agent 只能更新已存在任务，不支持创建或删除任务。
- 调度服务只负责执行 Playwright，不会自动调用 Healer 修复失败脚本。
