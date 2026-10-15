from pathlib import Path
p=Path('docs/superpowers/plans/2026-10-12-alphaagent-decision-research.md')
s=p.read_text(encoding='utf8')
start=s.index('## P6：市场扫描、主动更新和历史版本')
end=s.index('## P7：统一验收、消融评测与交付',start)
section=s[start:end]
for prefix in ('扩展至少第二个业务不同的子行业','显示市场/行业覆盖','刷新生成新任务','完整接通20/40/60交易日观察','贯通取消、超时、恢复','不添加定时通知'):
 old='- [ ] '+prefix
 assert old in section,prefix
 section=section.replace(old,'- [x] '+prefix,1)
note='''**P6 当前状态（2026-10-14）：部分交付，用户验收待进行，不能标整体完成。** 制造业有限原文模板、真实模型样板、手动刷新/版本对比、观察/撤回留痕、历史与导出已接通。中芯国际首次与更新分别4次模型调用，原69条完成记录的数据库内容未变。512项quant（含旧5.2）与91项前端通过。行业目录/成分源本轮不可用，完整市场分母未核定；市场只展示独立取得的真实指数观察，不发布行业排名。市场批量计算与热度实源验收两项保持未勾选，详见[P6结果与点击步骤](2026-10-14-alphaagent-decision-p6-results.md)。恢复接口已实测留痕，但源/预算限制未解除的诊断仍失败，不冒充真实模型成功恢复。20/40/60观察未成熟，60日尚缺2027日历；方法/预测价值未验证。P7未开始。\n\n'''
section=section.replace('**明确测试输入：**',note+'**明确测试输入：**',1)
s=s[:start]+section+s[end:]
s=s.replace('| 市场扫描、更新、版本、结果观察与恢复 | P6 | 未实施 |','| 市场扫描、更新、版本、结果观察与恢复 | P6 | 部分交付：制造样板、更新/跟踪/历史已自测；行业热度实源未齐，待用户验收 |')
s=s.replace('。P6尚未开始。','。P6进度见2026-10-14的[P6验收记录](2026-10-14-alphaagent-decision-p6-results.md)。',1)
p.write_text(s,encoding='utf8')
