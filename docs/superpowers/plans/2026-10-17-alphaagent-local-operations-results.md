# AlphaAgent 本机自启与自动备份验收

2026-10-17完成实际Windows任务注册、任务冷启动、完整备份和隔离恢复演练。沿用`D:\deepsearch\.phase3-quant-o\alpha-history-real-02`，8839端口与原历史ID保持不变。未清理、提交或推送已有工作区。

## 已安装任务

| Windows任务 | 触发与行为 | 本机验证 |
| --- | --- | --- |
| `AlphaAgent-Service` | 当前用户登录15秒后启动；每天09:00另一次启动检查。隐藏窗口，无需浏览器或Codex。任务跟随它启动的服务进程，无执行时长上限；进程异常最多重试3次，间隔2分钟。 | 实际关闭已核对身份且无研究运行的旧服务，通过任务启动新进程；健康ok，每日更新enabled，活动库标识一致。再次手动启动仍复用同一进程。 |
| `AlphaAgent-Backup` | 每天21:00、登录2分钟后检查；错过可补做。同一窗口已成功则跳过。后台运行，最多2小时，失败最多重试3次。 | 真实完整备份COMPLETED；再次触发任务退出0，完整快照目录数仍为2，没有额外重复。 |

任务属于当前登录用户，运行级别Limited、Interactive，无需保存Windows密码。使用系统Windows PowerShell，不依赖Codex缓存路径；执行策略仅对该进程生效。系统时区为China Standard Time。

安装脚本`D:\deepsearch\install-alphaagent-tasks.ps1`只更新带本项目标记的同名任务，碰到无关同名任务会拒绝。已实测重复安装。注册前校验活动库存在、备份和活动目录不重叠。备份目录DACL受保护，只允许当前用户、SYSTEM、Administrators完全访问；不会放宽现有活动库权限。

## 完整备份与恢复

- 备份根目录：`D:\deepsearch\backups\alphaagent`。
- 本轮完整快照：`20261017-224710-965afeba`，22:47:10开始、22:52:43完成，完整格式v2。
- `history/`包含在线SQLite快照及4243个数据库引用原文；`runtime/unreferenced-blobs/`另保存4013个已有CAS文件，共8256个。旧`live-research`的176份文件、其他活动目录文件、调用日志、知识／研究版本指针均另有补充清单。
- 本机路径配置、启动脚本与`.env`只在私密本地备份中保存，不发送模型或输出其内容。
- 数据库执行完整性、外键与引用校验；原文和补充文件核对SHA-256及清单，拒绝缺失、多余或被修改的文件。
- 在`backups\alphaagent\restore-drills\20261017-224710-965afeba`进行真实隔离恢复，并按补充清单恢复运行文件与额外CAS。恢复后的8256个CAS内容均符合其SHA-256身份，176份旧报告文件齐全。
- 恢复前后261条已有研究payload摘要逐条一致：missing=0、changed=0。在线活动库也再次通过相同核对，仍为261条。
- 演练目录保留隔离标记，`executor_started=false`；没有将它设为活动库，没有从备份发起研究或交易。

首次建立的旧格式快照也保留，最近成功指针已指向上述完整v2快照。不会自动删除或轮换任何已有快照。失败或空间不足保留以前成功备份，并记录最近失败，不用新空库替代现有数据。

## 自测结果

- `ruff check`：新增备份实现、入口与测试通过。
- 备份专项及既有配对恢复测试：**19 passed, 1 skipped**；跳过的是当前Windows沙箱账户不能创建符号链接的环境测试。
- 覆盖成功配对恢复、篡改拒绝、错过备份补做、窗口去重、失败保留上次成功、缺库／目录重叠拒绝、空间不足保护、版本指针在数据库快照前捕获。
- Windows原生PowerShell实际启动通过，修正了脚本中文在PowerShell5中的UTF-8 BOM兼容性。
- 任务冷启动后首页HTTP200，明日预测、投资计划、持仓顾问、账户调仓各一份既有报告均为COMPLETED，统一历史接口首屏读取50条；本轮没有生成新的模型研究。

机器证据保存在`D:\deepsearch\.phase3-quant-o\local-operations-20261017`：任务XML、cold-start.json、scheduler-acceptance.json、history-after.json、http-acceptance.json、restore-acceptance.json及backup-tests.log。证据不包含Token或账户原始内容。

## 日常使用与边界

每天登录Windows后打开`http://127.0.0.1:8839/#/`，无需启动终端。盘前清单仍需09:10前登录、联网且不休眠。电脑关闭、休眠或账户尚未登录时，本地服务不能工作。

尚未实际重启／注销电脑；已实测由真实Windows任务从无服务状态启动。已验证的是当前本机与现有数据，不代表外部行情和模型供应商永不中断。

备份当前也在D盘，不能防范整块磁盘损坏，建议将成功快照另存外置盘。当前不自动删除旧备份，需留意磁盘余量。正式恢复仍需完整校验、隔离演练和调用对账，不自动替换活动库。操作说明见[日常运行](../../alphaagent-daily-operation.md)。
