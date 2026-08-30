# 定时任务实现与调试说明

## 1. 目标与边界

定时任务能力用于把已经生成好的 Playwright 自动化脚本按 Cron 时间表达式周期执行。

当前实现分为两条链路：

1. 配置创建/更新链路：用户在前端对话里提供项目路径和执行计划，Master 识别为 `scheduler` 意图后，调用 `SchedulerAgent` 创建或更新该项目的系统托管任务。
2. 独立执行链路：`web-agent-scheduler` 命令启动一个常驻轮询服务，定期读取 JSON 配置文件，判断哪些任务到点，然后串行执行 `npx playwright test`。

注意：Scheduler Agent 会在配置文件、项目条目或系统托管任务不存在时自动创建；任务 ID 根据项目绝对路径稳定生成，不接受用户自定义。Scheduler Agent 不直接执行测试，仍由独立调度服务扫描执行。

## 2. 总体流程

```text
用户对话
  -> Master Intent 判断为 scheduler
  -> 提取 project_name/project_dir、schedule_cron 等参数
  -> SchedulerAgent.upsert_auto_scheduled_task_config()
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

推荐进入后端目录运行：

```bash
cd web-agent
uv run web-agent-scheduler
```

指定配置文件：

```bash
uv run web-agent-scheduler --config ./scheduler_tasks.json
```

默认配置文件路径来自环境变量解析：

```text
SCHEDULER_CONFIG_PATH 非空时：使用该路径；相对路径以服务端 web-agent 根目录为基准
SCHEDULER_CONFIG_PATH 为空时：使用服务端 web-agent/scheduler_tasks.json
```

配置文件属于服务端运行配置，不放在被执行的自动化项目里。默认路径是：

```text
<仓库根目录>/web-agent/scheduler_tasks.json
```

独立调度服务启动时只需确定这一个配置文件；每个 `projects[]` 条目里的 `project_dir` 再指向实际要执行的自动化项目。

## 4. 如何配置定时任务

可参考 [web-agent/scheduler_tasks.example.json](/Users/jin/Documents/code/github/web-test-agent/web-agent/scheduler_tasks.example.json)：

```json
{
  "scheduler": {
    "poll_interval_seconds": 30,
    "task_timeout_seconds": 1800,
    "max_pending_runs": 100,
    "misfire_grace_seconds": 300
  },
  "projects": [
    {
      "project_name": "demo",
      "test_root_dir": "test_case",
      "timezone": "Asia/Shanghai",
      "headed": false,
      "tasks": [
        {
          "task_id": "scheduled-demo-0000000000",
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
- `scheduler.task_timeout_seconds`：单次 Playwright 任务的最大执行时间，超时后会结束完整进程树。
- `scheduler.max_pending_runs`：串行队列的最大待执行任务数；同一个任务积压时只保留最新一次。
- `scheduler.misfire_grace_seconds`：轮询延迟时向前补偿检查 Cron 命中窗口的最长时间。
- `projects[].project_name`：自动化项目名。未配置 `project_dir` 时，会解析为 `DEFAULT_AUTOMATION_PROJECT_ROOT/project_name`。
- `projects[].project_dir`：自动化项目目录。相对路径会相对 `DEFAULT_AUTOMATION_PROJECT_ROOT` 解析；绝对路径按原样解析。
- `projects[].test_root_dir`：测试根目录，默认是 `test_case`。调度日志会写到这个目录下。
- `projects[].timezone`：项目时区，例如 `Asia/Shanghai`。为空时使用服务进程本地时区。
- `projects[].headed`：项目默认浏览器模式，`true` 有头，`false` 无头。
- `projects[].tasks[]`：项目下的任务列表，`task_id` 在同一项目内必须唯一。
- `tasks[].task_id`：系统根据项目绝对路径生成的只读任务 ID，用户无需也不能在对话中指定。
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

## 8. 对话里如何创建或更新定时任务

对话入口只需要项目路径和具体执行计划，示例：

```text
/Users/jin/webautotest/demo 把这个项目设置为每天上午 10:15 执行
```

```text
项目路径=/Users/jin/webautotest/demo，每周一 09:00 执行
```

Master 会尽量抽取这些字段：

- `project_name` 或 `project_dir`
- `schedule_cron`
- `schedule_headed`
- `schedule_enabled`
- `schedule_locations`

必要参数：

- scheduler 意图至少需要 `project_name` 或 `project_dir`
- 必须有具体执行计划，并换算为 `schedule_cron`
- 不收集 `schedule_task_id`；即使用户提供也会忽略

如果配置文件、对应项目或系统托管任务不存在，会自动创建；同一项目再次设置时更新原任务，不会产生重复任务。`locations` 未提供时执行项目内全部 Playwright 测试。

## 9. 相关文件职责

- [web-agent/deep_agent/scheduler/cli.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/cli.py)：命令行入口，解析 `--config`，创建并启动 `SchedulerService`。
- [web-agent/deep_agent/scheduler/service.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/service.py)：调度执行核心。负责轮询配置、计算到点任务、串行排队、启动 Playwright、写项目日志。
- [web-agent/deep_agent/scheduler/models.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/models.py)：配置文件 Pydantic 模型。负责字段默认值、归一化、项目和任务唯一性校验、时区校验。
- [web-agent/deep_agent/scheduler/store.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/store.py)：配置文件读写和路径解析。负责加载 JSON、保存 JSON、解析项目目录、生成系统任务 ID 并创建/更新托管任务。
- [web-agent/deep_agent/scheduler/cron.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/scheduler/cron.py)：五段 Cron 解析和命中判断。
- [web-agent/deep_agent/agent/scheduler/scheduler_agent.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/agent/scheduler/scheduler_agent.py)：对话中的 Scheduler Agent，根据 Master 提取的项目路径和执行计划创建或更新配置。
- [web-agent/deep_agent/agent/master/models/intent.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/agent/master/models/intent.py)：声明 scheduler 意图字段和缺参规则。
- [web-agent/deep_agent/web_autotest_agent_workflow.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/deep_agent/web_autotest_agent_workflow.py)：主图把 `scheduler` 路由到 `scheduler_config_node`。
- [web-agent/scheduler_tasks.example.json](/Users/jin/Documents/code/github/web-test-agent/web-agent/scheduler_tasks.example.json)：配置文件示例。
- [web-agent/tests/test_scheduler_service.py](/Users/jin/Documents/code/github/web-test-agent/web-agent/tests/test_scheduler_service.py)：调度配置更新、串行执行和日志行为测试。

## 10. 调试建议

### 10.1 先准备最小配置

```bash
mkdir -p ~/webautotest/demo/test_case
cp web-agent/scheduler_tasks.example.json web-agent/scheduler_tasks.json
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
uv run --project web-agent web-agent-scheduler
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

config = load_scheduler_config(Path("web-agent/scheduler_tasks.json").resolve())
print(config.model_dump(exclude_none=True))
PY
```

### 10.6 常见问题

- 配置文件不存在：先通过对话提交项目路径和执行计划，Scheduler Agent 会自动创建默认配置文件。
- 任务不执行：检查 `enabled` 是否为 `true`，Cron 是否命中当前项目时区。
- 找不到项目：检查 `project_name` 是否对应 `DEFAULT_AUTOMATION_PROJECT_ROOT/project_name`，或直接使用 `project_dir`。
- 找不到脚本：`locations` 是相对 `project_dir` 的路径，不能相对仓库根目录理解。
- 有任务冲突：当前实现是串行队列，同一时间多个任务都会排队执行。
- 对话配置失败：检查项目目录是否真实存在，并确认执行计划能够转换为合法的五段 Cron。

## 11. 当前实现限制

- 只支持五段 Cron，不支持秒级 Cron。
- 调度服务是单进程内存去重；重启服务后 `_last_scheduled_minutes` 会清空。
- 所有到点任务串行执行，没有并发执行策略。
- 队列没有最大长度限制；高频任务或长耗时任务可能堆积。
- Scheduler Agent 每个项目维护一个系统托管任务，不支持用户自定义任务 ID，也不提供删除任务能力。
- 调度服务只负责执行 Playwright，不会自动调用 Healer 修复失败脚本。
