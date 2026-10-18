# 重启后自启与终端弹窗修复

2026-10-18用户实际重启验收：系统开机12:30:10，AlphaAgent-Service在12:30:38触发，后端12:30:44启动。健康接口ok，每日更新enabled，首页HTTP200；活动库实例标识仍对应原alpha-history-real-02目录。证明真实重启后自启有效。

用户截图显示Windows Terminal中常驻PowerShell并打印AlphaAgent地址。原任务直接运行控制台子系统的powershell.exe，虽然带WindowStyle Hidden，仍依赖控制台宿主对隐藏窗口的处理；TaskMode继续等待它启动的服务，所以窗口不会退出。这不是前端或后端未完成启动。

本轮修复只改变Windows任务启动入口：GUI子系统的pythonw.exe运行scripts/start_alphaagent_background.py，以CREATE_NO_WINDOW启动原PowerShell脚本，重定向诊断到logs/alphaagent-startup.log，等待子进程并将退出码传回任务计划。原数据检查、已有实例复用和服务异常重试保持不变；不修改全局默认终端设置。备份时刻、路径和历史保持原样。

验证：

- 无窗口监督入口单测两项通过：保持原启动参数和退出码；实际启动PowerShell，GetConsoleWindow返回0，并正确传播非零退出码。
- 更新真实系统任务后，从无服务状态重新启动，健康ok、每日更新enabled、首页HTTP200，任务Running。
- 切换前无RUNNING或PENDING研究。261条研究payload摘要前后完全一致，数据库quick_check通过。
- 证据目录：D:\deepsearch\.phase3-quant-o\local-operations-20261018。

修复后已实测系统任务冷启动，尚未再次重启整台电脑。后台同时提供构建好的Vue前端与后端API，无需另开Vite服务；日常入口仍为http://127.0.0.1:8839/#/。
