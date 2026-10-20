# 当前正式发布的全部投资参考规则

> 导出时间：2026-10-20T09:23:23+08:00。共30条。以下为实际字段的阅读版，不新增规则或改写原文。

> 所有规则均是REFERENCE_ONLY／UNVALIDATED：来源与规则审核不等于盈利或预测有效性证明。

## 1. 偿债能力先核对资金可用性而非只看余额

- 规则ID：`KR-LIQUIDITY-USABLE-CASH`；类型：风险；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`a8e25a924a0c41d9b03ada355ff65905b13bfd00291504fd40b7df48e00b301d`。
- [完整原始规则JSON](rules/KR-LIQUIDITY-USABLE-CASH.json)

### 来源主张

财政部现金流量表准则将现金界定为库存现金和可随时支付的存款，并要求附注披露现金等价物构成、对应资产负债表金额及集团内不可使用的大额现金。

### 系统规则

评估流动性时分别核对货币资金、现金及现金等价物和受限或无法跨主体使用的金额，再与同一主体的到期债务、可验证资金来源及经营资金需求比较；账面资金总额不能直接视为可偿债余额。

### A股适配说明

依据官方现金定义及披露要求补充系统流动性检查；受限原因、期限匹配和营运需求是本系统核查项，不是作者给出的固定安全比例，也不是法定偿债能力结论。

### 需要的数据

- 货币资金与现金等价物附注
- 受限用途和解限期限
- 主体间资金使用约束
- 债务期限和契约
- 经营资金需求与可验证资金来源

### 适用条件

- 资产与债务必须主体、币种和时间匹配，不把所有货币资金与现金等价物差额机械认定为受限资金。
- 金融企业需结合流动性监管口径和存贷款业务；资金用途不明时保留缺口，不出确定安全结论。

### 分析步骤

- 逐项解释账面资金和现金等价物差额，核对可动用时间与用途。
- 与到期债务、经营需求和可验证融资来源匹配，给出证据支持的风险解释及缺口。

### 反例与失效环境

- 保证金可能在特定债务结算时释放，不能简单永久剔除；账面现金充足但位于无法上划的子公司仍可能无法偿还母公司债务；未使用授信不等同到账现金。

### 禁止推断

- 不得把货币资金余额全部当作可用现金，或只凭现金与负债固定比值认定安全、造假、必然违约或自动买卖。

### 关键词与冲突分组

关键词：货币资金、受限资金、现金等价物、偿债、流动性、到期债务。冲突分组：资金余额与实际可用性。

### 固定原文引用

- `KS-MOF-CAS31`；版本`c29913b3fc63927d394ed8bf87654ca13b76a88739d7b320f4ce0ac45e58c2c4`；字符区间`[51, 240)`。

第二条 现金流量表，是指反映企业在一定会计期间现金和现金等价物流入和流出的报表。 现金，是指企业库存现金以及可以随时用于支付的存款。 现金等价物，是指企业持有的期限短、流动性强、易于转换为已知金额现金、价值变动风险很小的投资。 本准则提及现金时，除非同时提及现金等价物，均包括现金和现金等价物。 第三条 合并现金流量表的编制和列报，适用《企业会计准则第33号——合并财务报表》。

- `KS-MOF-CAS31`；版本`c29913b3fc63927d394ed8bf87654ca13b76a88739d7b320f4ce0ac45e58c2c4`；字符区间`[1996, 2162)`。

第十八条 企业应当在附注中披露不涉及当期现金收支、但影响企业财务状况或在未来可能影响企业现金流量的重大投资和筹资活动。 第十九条 企业应当在附注中披露与现金和现金等价物有关的下列信息： （一）现金和现金等价物的构成及其在资产负债表中的相应金额。 （二）企业持有但不能由母公司或 集团内 其他子公司使用的大额现金和现金等价物 金 额。

## 2. 利润与经营现金流差异先用附注调节解释

- 规则ID：`KR-PROFIT-CASH-BRIDGE`；类型：筛选；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`f605eb3897db8086a29543a6f1563b7043d94168f12d6059fba3fc1315c55d15`。
- [完整原始规则JSON](rules/KR-PROFIT-CASH-BRIDGE.json)

### 来源主张

财政部现金流量表准则要求披露净利润到经营活动现金流量的调节项目，包含折旧、减值、投资损益、存货和经营性应收应付；金融企业可按行业确定经营现金流项目。

### 系统规则

比较净利润与经营现金流时，先统一报表主体和期间，依据附注拆解非现金项目、营运资本和非经营损益，再结合业务解释差异及持续性；差异或单期负现金流本身不能证明造假。

### A股适配说明

这是依据财政部原文形成的系统研究检查项，不冒充唐朝未取得章节的原话。区别于已有现金流证据不足规则，本条具体规定差异的调节解释步骤，不设固定比值阈值。

### 需要的数据

- 同主体同期间净利润与经营现金流
- 现金流调节附注
- 存货、经营应收和应付变动
- 非现金项目与非经营损益
- 行业特点和可比期间披露

### 适用条件

- 净利润与经营现金流必须主体、期间可比，不能将归母净利润直接替代包含少数股东损益的净利润作无差异调节。
- 银行等金融企业另核行业格式与业务，缺少附注时明确缺项，不虚构调节数。

### 分析步骤

- 把净利润调节为经营现金流，并核对差额是否能被已披露项目解释。
- 区分季节性、扩张占款、非现金费用与持续回款恶化，结合后续回款证据列出反证。

### 反例与失效环境

- 季节性备货或增长期占款可能使经营现金流暂时低于利润而非造假；增加应付款或收取预付款也可暂时抬高现金流，正现金流不保证利润质量。

### 禁止推断

- 不得以现金流低于净利润或现金流为负直接定性财务造假，也不得以比值大于某个固定数保证企业安全或股价上涨。

### 关键词与冲突分组

关键词：现金流、净利润、调节附注、应收、存货、非现金项目。冲突分组：利润现金差异与异常定性。

### 固定原文引用

- `KS-MOF-CAS31`；版本`c29913b3fc63927d394ed8bf87654ca13b76a88739d7b320f4ce0ac45e58c2c4`；字符区间`[1598, 1846)`。

第十六条 企业应当在附注中披露将净利润调节为经营活动现金流量的信息。至少应当单独披露对净利润进行调节的下列项目： （一）资产减值准备； （二）固定资产折旧； （三）无形资产摊销； （四）长期待摊费用摊销； （五）待摊费用； （六）预提费用； （七）处置固定资产、无形资产和其他长期资产的损益； （八）固定资产报废损失； （九）公允价值变动损益； （十）财务费用； （十一）投资损益； （十二）递延 所得税资产和递延所得税负债 ； （十三）存货； （十四）经营性应收项目； （十五）经营性应付项目。

- `KS-MOF-CAS31`；版本`c29913b3fc63927d394ed8bf87654ca13b76a88739d7b320f4ce0ac45e58c2c4`；字符区间`[1062, 1401)`。

第十一条 金融企业可以根据行业特点和现金流量实际情况，合理确定 经营活动 现金流量项目的类别。 第四章 投资活动现金流量 第十二条 投资活动，是指企业长期资产的购建和不包括在现金等价物范围的投资及其处置活动。 第十三条 投资活动产生的现金流量至少应当单独列示 反映 下列 信息的 项目： （一）收回投资收到的现金； （二）取得投资收益收到的现金； （三）处置固定资产、无形资产和其他长期资产收回的现金净额； （四）处置子公司及其他营业单位 收到 的现金净额； （ 五 ）收到其他与投资活动有关的现金； （ 六 ）购建固定资产、无形资产和其他长期资产支付的现金； （ 七 ）投资支付的现金； （八）取得子公司及其他营业单位支付的现金净额； （ 九 ）支付其他与投资活动有关的现金。

## 3. 集团经营与母公司分配能力分开核对

- 规则ID：`KR-FINANCIAL-ENTITY-SCOPE`；类型：风险；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`f7a9c8959df367fe64be9dc165c51f566b4a75787a3d158f849c2c3cb3fd1209`。
- [完整原始规则JSON](rules/KR-FINANCIAL-ENTITY-SCOPE.json)

### 来源主张

唐朝公开书摘区分合并报表的会计主体与法律实体，并指出盈余公积相关计算使用母公司利润口径；财政部合并财务报表准则定义集团合并报表和控制范围。

### 系统规则

研究集团经营质量时明确合并范围；讨论上市母公司的分红或偿债能力时另查母公司报表、子公司资金上划与约束，不能用集团利润和现金直接替代母公司的可分配利润与可用现金。

### A股适配说明

书摘仅为旧版公开片段，不代表新准则升级版全文；本规则采用主体区分思想，不采用书摘中的历史法律比例。分配金额和合法性另按适用时点法律、章程及公告核查。

### 需要的数据

- 同一报告期的合并与母公司报表
- 合并范围及变更附注
- 子公司分红与资金上划资料
- 分配方案、债务和资金使用约束

### 适用条件

- 所有引用数字注明主体、报告期和币种；缺少母公司或上划资料时，分配能力只能作条件性判断。

### 分析步骤

- 先区分集团经营事实与母公司自身的利润、现金和债务。
- 检查可上划资金及限制；涉及分红金额时另核现行规则和公司公告。

### 反例与失效环境

- 控股型母公司可能依靠合法子公司分红获得现金，不能因母公司主营收入少就认定不能分红；集团盈利充足也可能因子公司限制而无法及时上划。

### 禁止推断

- 不得把合并现金直接视为母公司可用现金，或仅凭账面未分配利润承诺分红金额；不得把旧版书摘比例当作现行通用法律规则。

### 关键词与冲突分组

关键词：母公司、合并报表、分红、主体、可用现金。冲突分组：集团经营与法律实体分配。

### 固定原文引用

- `KS-TANG-XHSD-EXCERPT`；版本`a4948bf0b1f1ee15a599c6a53d09c514cbd1a0af13eeeea74fd208de70d65452`；字符区间`[365, 474)`。

请投资者注意一点，法定盈余公积的10％提取要求，是以上市公司本部净利润（母公司利润表净利润）为基数的。“法定”管的是法律实体。合并报表是会计意义上的单位，不是法律意义的公司。投资人关心合并报表习惯了，很容易忘记这一点。

- `KS-MOF-CAS33`；版本`fa895f8dcecf7c012bc3e2169d189c8098ed172fd8995cc10e20331ac75ea2ec`；字符区间`[53, 322)`。

第二条 合并财务报表，是指反映母公司和其全部子公司形成的企业集团整体财务状况、经营成果和现金流量的财务报表。 母公司，是指控制一个或一个以上主体（含企业、被投资单位中可分割的部分，以及企业所控制的结构化主体等,下同）的主体。 子公司，是指被母公司控制的主体。 第三条 合并财务报表至少应当包括下列组成部分： （一）合并资产负债表； （二）合并利润表； （三）合并现金流量表； （四）合并所有者权益（或股东权益，下同）变动表； （五）附注。 企业集团中期期末编制合并财务报表的，至少应当包括合并资产负债表、合并利润表、合并现金流量表和附注。

## 4. 逆向判断需要解释共识哪里错

- 规则ID：`KR-CONTRARIAN-EVIDENCE`；类型：风险；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`1143b0f964067b21425e83896fc9d510a3a001ed9b468e5cc19b97b569992611`。
- [完整原始规则JSON](rules/KR-CONTRARIAN-EVIDENCE.json)

### 来源主张

马克斯强调极端情绪下的逆向与逢共识必反不同；需理解群体在做什么、为何如此、哪里错以及替代选择的依据。

### 系统规则

只有估值、经营与可验证情绪证据显示极端且反向判断有具体理由时，才讨论有约束的风险调整；反对市场共识本身不构成买卖依据。

### A股适配说明

把作者公开反向投资边界转成A股研究准入检查，不将情绪极端或逆向态度直接换算成胜率、仓位或必然反转。

### 需要的数据

- 可核查的群体观点与行为
- 估值和经营证据
- 可承受期限与流动性
- 反向论证及失效条件

### 适用条件

- 缺少估值、经营或流动性边界等关键证据时只能观察等待，不能用情绪标签直接触发交易。

### 分析步骤

- 分别说明群体行为、行为原因、错误所在和替代选择依据。
- 核对永久损失风险、可承受期限及反证，保留共识可能正确的解释。

### 反例与失效环境

- 共识可能来自持续改善的基本面，盲目逆向会长期亏损；恐慌也可能反映永久损失而不是便宜机会。

### 禁止推断

- 不得以人多看多就看空、人多看空就抄底，或把逆向投资当作没有风险的必胜方法。

### 关键词与冲突分组

关键词：逆向投资、共识、情绪、风险、反证。冲突分组：逆向与趋势纪律。

### 固定原文引用

- `KS-MARKS-TEMPERATURE-2023`；版本`22d82c764cf95fb6e077fd118a299fbc7a52335172bca02e9b5795a04e5b6891`；字符区间`[30666, 31325)`。

Remember that in extreme times, because of the above, the secret to making money lies in contrarianism, not conformity. When emotional investors take an extreme view of an asset’s future and, as a result, take the price to unjustified levels, the “easy money” is usually made by doing the opposite. This is, however, very different from simply diverging from the consensus all the time. Indeed, most of the time, the consensus is as close to right as most individuals can get. So to be successful at contrarianism, you have to understand (a) what the herd is doing, (b) why it’s doing it, (c) what’s wrong with it, and (d) what should be done instead and why.

## 5. 周期判断先说明因果链而非套固定日历

- 规则ID：`KR-CYCLE-CAUSAL-BOUNDARY`；类型：市场环境；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`48392f2284bd5fa2355a1ffe8e59c4ae98d925cc6a49c2a4577cfb754dd4f7a5`。
- [完整原始规则JSON](rules/KR-CYCLE-CAUSAL-BOUNDARY.json)

### 来源主张

马克斯在官方备忘录解释《周期》的定义：周期是一连串相互导致的事件，经济与投资者心理可能走向过度并随后修正。

### 系统规则

评估市场环境时记录经营、信用、估值和群体情绪的因果链，把周期位置作为风险姿态的条件性参考，不按固定日历或单一涨跌幅断言拐点。

### A股适配说明

将作者公开周期定义转成A股证据检查项，经营与信用指标为本系统需要核查的输入，不冒充作者给出的量化模型或周期时钟。

### 需要的数据

- 同一截止时点的经营与信用事实
- 估值与群体情绪证据
- 因果假设与反证
- 资金期限与风险约束

### 适用条件

- 有可核查的同窗证据并注明缺项与不确定性；没有证据时不得给市场贴确定的周期阶段标签。

### 分析步骤

- 区分已观察事实和周期传导假设，解释每个环节如何影响下一环节。
- 列出能推翻周期定位的变化，再讨论有约束的风险姿态调整。

### 反例与失效环境

- 宽松资金可能使高估持续，信用收缩也可能令低估更便宜；周期判断合理不代表反转及时发生。

### 禁止推断

- 不得断言周期有固定长度、预测准确拐点日期、保证反转，或仅凭该规则给出明日方向及无条件买卖指令。

### 关键词与冲突分组

关键词：周期、因果链、市场环境、估值、情绪、信用。冲突分组：周期定位与交易时机。

### 固定原文引用

- `KS-MARKS-TEMPERATURE-2023`；版本`22d82c764cf95fb6e077fd118a299fbc7a52335172bca02e9b5795a04e5b6891`；字符区间`[33478, 34469)`。

On cycles : In my book Mastering the Market Cycle , I defined cycles not as a series of up and down movements, each of which regularly precedes the next – which I believe is the usual definition – but as a series of events, each of which causes the next. This causality holds the key to understanding cycles. In particular, I think economies, investor psychology, and thus markets eventually go too far in one direction or another – they become too positive or too negative – and afterward they eventually swing back toward moderation (and then usually toward excess in the opposite direction). Thus, in my opinion, these cycles are best understood as stemming from “excesses and corrections.” Overlooking the details of the individual episodes, it’s clear from the descriptions of these five calls that the greatest opportunities for bargain purchases result from overly negative prevailing psychology and the greatest opportunities to sell at too-high prices arise from excessive optimism.

## 6. 比较投资结果先核对成本后的净收益

- 规则ID：`KR-COST-NET-RETURN`；类型：筛选；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`448030389b2062bcedf6c84091554d84048d9304f44da412e4b5229c44e8ebd1`。
- [完整原始规则JSON](rules/KR-COST-NET-RETURN.json)

### 来源主张

博格在公开原讲稿中区分市场毛收益与投资者扣除中介成本后的净收益，并讨论费用与税对实际所得的影响。

### 系统规则

比较基金或投资路线时列明管理、托管、交易、换手和适用税费，使用同窗同口径的成本后收益与基准；费用缺项时不能宣称净收益优势。

### A股适配说明

这是结合博格公开成本论述形成的A股研究检查项，不是《投资常识》整书蒸馏；本地费率、税制与净值口径须重新核查，未验证收益效果。

### 需要的数据

- 产品费率与净值口径
- 换手和交易成本
- 适用税费
- 同窗净收益与风险可比基准

### 适用条件

- 比较对象的风险暴露和期限可比；费用或税制未知须明确缺口，不把美国历史平均费率当作当前A股参数。

### 分析步骤

- 核对费率与换手资料，避免重复扣除已反映在净值中的费用。
- 分别列出毛收益、净收益和基准，不能用低费率代替风险判断。

### 反例与失效环境

- 低费率产品仍可能因集中、跟踪偏差或流动性损失跑输；高费率策略个别时期可能在扣费后胜出，低成本不保证未来盈利。

### 禁止推断

- 不得宣称主动投资必然亏损、指数基金必然盈利，或凭作者美国历史样本保证A股净收益与明日涨跌。

### 关键词与冲突分组

关键词：费用、成本、净收益、指数基金、基线、换手。冲突分组：成本与风险可比性。

### 固定原文引用

- `KS-BOGLE-COST-2005`；版本`3772add96e8fbaaa1b372dcafa82a8d522878a05a55d5d796abf1ea956aeb375`；字符区间`[8089, 8571)`。

As a group, investors never— never!—enjoy the gross return that the markets deliver. Investors earn the net return, after all of the costs of our system of financial intermediation. Thus, just as gambling in the casino is a zero-sum game before the croupiers rake in their share (I’m told that this is called “vigorish,” or “the vig”) and a loser’s game thereafter, so beating the stock and bond markets is a zero-sum game before intermediation costs, and a loser’s game thereafter.

- `KS-BOGLE-COST-2005`；版本`3772add96e8fbaaa1b372dcafa82a8d522878a05a55d5d796abf1ea956aeb375`；字符区间`[11541, 11987)`。

And that’s before taxes. Because of the shocking tax inefficiency engendered by its astonishingly high (100-percent-plus) portfolio turnover, the average managed equity fund cost its taxable investors another 2.2 percentage points in taxes, producing not 57 percent of the market’s annual return, but only 41 percent. With the index fund relinquishing only 0.9 points in taxes, the gap between the equity fund and the index fund rises from 2.8 to

## 7. 选择能够持续执行的风险配置

- 规则ID：`KR-BEHAVIOR-FIT`；类型：容量；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`4a08b1ed8aea145f8cdd00050b54a8b8ba509452f6201a5383c7490a3dbb12dc`。
- [完整原始规则JSON](rules/KR-BEHAVIOR-FIT.json)

### 来源主张

Housel认为模型追求风险调整回报，而现实投资者还关心睡得安稳等心理和生活约束。

### 系统规则

路线与风险配置要纳入用户能够遵守的约束；无法承受的理论最优配置需要调整并说明机会成本。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 风险承受偏好
- 现金需求
- 执行约束

### 适用条件

- 这是行为与适配原则，不是来源作者给出的通用资产比例或本系统收益优化结果。

### 分析步骤

- 路线与风险配置要纳入用户能够遵守的约束；无法承受的理论最优配置需要调整并说明机会成本。

### 反例与失效环境

- 主观舒适可能导致过度保守或追逐近期表现，仍需检查成本、分散和实际财务目标。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：容量、风险偏好、行为、路线选择。冲突分组：风险与容量。

### 固定原文引用

- `KS-HOUSEL-REASONABLE`；版本`73fe4c0624bd05d0d15fb36340bad9a054994650d2e4bb866cc016293433895d`；字符区间`[2772, 3132)`。

My theory is that most investment models maximize for risk-adjusted returns, but in the real world every investor wants to maximize for sleeping well at night and being proud of themselves in a complicated world. Harry Markowitz won the Nobel Prize for exploring the mathematical tradeoff between risk and return. A few years ago the Wall Street Journal asked 

## 8. 持有期限必须有现金流和承受能力支撑

- 规则ID：`KR-HORIZON-ENDURANCE`；类型：持有；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`7664fd879dc2d113316b16e310806075fc21f653daa51fbd45fa87a73c3016a2`。
- [完整原始规则JSON](rules/KR-HORIZON-ENDURANCE.json)

### 来源主张

Housel指出长期视角需要配置、现金流和心态能够应对短期发生的问题，长期计划本身不等于坚持能力。

### 系统规则

声明中长期持有前核查现金需求和阶段性亏损承受能力，按固定条件复核而非用长期口号回避短期风险。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 现金需求
- 配置约束
- 阶段亏损承受能力

### 适用条件

- 期限和承受能力分别确认，不能以用户选择未来几个月就假定能够承受全部波动。

### 分析步骤

- 声明中长期持有前核查现金需求和阶段性亏损承受能力，按固定条件复核而非用长期口号回避短期风险。

### 反例与失效环境

- 基本面长期未变但用户被迫赎回或需要现金时，理论上的长期回报可能无法获得。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：持有、期限、现金流、回撤控制。冲突分组：风险与容量。

### 固定原文引用

- `KS-HOUSEL-ENDURANCE`；版本`3617a3130ffc357a316eb98689a1be3972c3f29ab6a872b884991bcbd5e962dc`；字符区间`[2899, 3159)`。

Having the endurance to make long time horizons work means your allocation, cash flow, and mindset are intentionally designed to deal with the nonsense that occurs within short time horizons. Tyler Cowen wrote: Plenty of companies have made big mistakes from t

## 9. 给未知事件保留调整空间

- 规则ID：`KR-KEEP-OPTIONS`；类型：容量；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`49aaac79e8558991fe5999afd5856e1614eace6817147a32460fb34b38148cfd`。
- [完整原始规则JSON](rules/KR-KEEP-OPTIONS.json)

### 来源主张

Housel强调保留选择空间，并尊重尚未知晓的风险，因为未准备应对的小事件也可能放大影响。

### 系统规则

资金使用和持仓安排应留出应对意外与调整判断的空间，缓冲大小作为待核算约束而非统一百分比。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 可用现金
- 持仓约束
- 未知事件情景

### 适用条件

- 现金和流动性缓冲依个人需求而定；原文没有本系统统一仓位上限。

### 分析步骤

- 资金使用和持仓安排应留出应对意外与调整判断的空间，缓冲大小作为待核算约束而非统一百分比。

### 反例与失效环境

- 过度持币也会有机会成本；保留现金不能消除已有持仓的跳空或系统性风险。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：容量、仓位、现金缓冲、未知风险。冲突分组：风险与容量。

### 固定原文引用

- `KS-HOUSEL-RISK`；版本`c4930c292d3bac7625907e26a0eead88ec1c459f43f538882d8f45bdb06543a5`；字符区间`[2273, 3183)`。

The first thing you have to do is value having options. I remember a driver’s ed class where the instructor said something like, “No matter where you are on the road, always have enough room to stop and a place to pull over.” It gets dangerous when you’re boxed in with limits on how you can react to something you didn’t expect. You’ll regret wanting to do something but not being able to do it. The second is a religious appreciation for unknowns . There’s all kinds of stuff out there that you don’t know about, and stuff that doesn’t exist yet you’ll eventually learn about. Unknowns are dangerous because even if they’re small, your lack of preparedness to deal with them is like an embedded form of leverage. This is the basis for wanting options and having “an out,” even – or especially – when you don’t think you’ll need it. Knock those two out and you’re ready to take some risk. Good risks are those

## 10. 先排除难以恢复和难以逆转的损失

- 规则ID：`KR-AVOID-RUIN`；类型：风险；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`1615027d53ef498d79c7b62329d3b1fbf35be50078f2ade40b44e5e826cb9773`。
- [完整原始规则JSON](rules/KR-AVOID-RUIN.json)

### 来源主张

Housel提醒难以逆转的承诺和严重到无法恢复的下行风险，集中非流动资产与不可解除债务是例子。

### 系统规则

研究投资路线前检查债务、集中与流动性约束，优先列出哪些失败会破坏持续参与能力。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 债务约束
- 组合集中度
- 资产流动性
- 可承受损失

### 适用条件

- 可承受损失需用户约束和工具核算，不能由模型自行推定或保证停损可成交。

### 分析步骤

- 研究投资路线前检查债务、集中与流动性约束，优先列出哪些失败会破坏持续参与能力。

### 反例与失效环境

- 持仓分散但都受同一融资风险影响时，表面分散仍可能发生共同损失。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：风险、避免破产、杠杆、集中度。冲突分组：风险与容量。

### 固定原文引用

- `KS-HOUSEL-RISK`；版本`c4930c292d3bac7625907e26a0eead88ec1c459f43f538882d8f45bdb06543a5`；字符区间`[1270, 2120)`。

One is a decision that can’t easily be reversed . What you value changes over time. So long-term commitments that can’t be reneged on – concentrated illiquid assets, or debt that can’t be discharged – is a risk landmine. Stuff you can change your mind about – careers, employers, friends, liquid investments – don’t have to be long-term regrettable, even if dealing with them hurts for a while. Another is a decision with downside so severe that it prevents eventual recovery . Getting wiped out. Getting banned. Getting convicted. Severe reputational harm. You’ll regret decisions that lead you there because their stench will stick to you long after you try to scrub them off and move on. So risk management comes down to serially avoiding decisions that can’t easily be reversed, whose downsides will demolish you and prevent recovery. I think tha

## 11. 风险还包括亏损和个人目标失败

- 规则ID：`KR-RISK-NON-VOLATILITY`；类型：风险；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`47ed5f4389abbdc67f3154cc8fe83f0c61407114d16df30b6ac7c246d40ec6cf`。
- [完整原始规则JSON](rules/KR-RISK-NON-VOLATILITY.json)

### 来源主张

Marks不认同把风险等同波动，并指出未来风险难以用唯一客观的量化尺度衡量。

### 系统规则

波动指标之外还检查永久损失、现金需求和目标偏离，不把低历史波动写成安全结论。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 波动序列
- 亏损情景
- 现金需求
- 投资目标

### 适用条件

- 风险量化需要明确口径，历史波动仅是部分观察，不代表完整未来风险。

### 分析步骤

- 波动指标之外还检查永久损失、现金需求和目标偏离，不把低历史波动写成安全结论。

### 反例与失效环境

- 长期低波动的高杠杆或缺乏流动性资产，仍可能在单次事件中损失严重。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：风险、波动、永久损失、回撤控制。冲突分组：风险与容量。

### 固定原文引用

- `KS-MARKS-RISK`；版本`91018cf8e1432cbc923b550004e05ebc8490384a6d9b59002ad4bb80df243351`；字符区间`[11514, 12344)`。

Rejecting risk as synonymous with volatility, as I do, eliminates the one measure of risk that’s entirely quantifiable, objective and absolute. This, in turn, makes it hard to argue that the market’s an efficient machine that precisely assesses the risk of each investment and allocates prospective return proportionately. 3BUMeasuring Risk Prospectively I’m sure we agree that investors should and do demand higher prospective returns on riskier investments. And hopefully we can agree that losing money is the risk people care about most in demanding prospective returns, and thus in setting prices for investments. An important question remains: How do they measure that risk? 5 © Oaktree Capital Management, L.P. All Rights Reserved  First, it clearly is nothi ng but a matter of opinion: hopefully an educated, skillful esti

## 12. 用现金需求日期约束可承受的流动性

- 规则ID：`KR-CAPACITY-LIQUIDITY`；类型：容量；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`17b8c791d28f51cedfd74d810fbfcf22dc0bac01cd0fb8e85c1a9b67322fd40a`。
- [完整原始规则JSON](rules/KR-CAPACITY-LIQUIDITY.json)

### 来源主张

Marks把需要时无法以合理价格变现视为个人风险，流动性需求不同的投资者承受能力不同。

### 系统规则

把投资期限、近期现金用途与证券可成交能力一起核查，资金规模不能单独决定适合哪条路线。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 现金需求日期
- 可投资资金
- 证券成交能力

### 适用条件

- 实际数量上限必须由工具结合交易规则与成交数据核算，本规则只提出约束。

### 分析步骤

- 把投资期限、近期现金用途与证券可成交能力一起核查，资金规模不能单独决定适合哪条路线。

### 反例与失效环境

- 账面收益良好但急需现金时无法卖出，仍可能导致实际财务目标失败。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：容量、资金量、流动性、现金需求、仓位。冲突分组：风险与容量。

### 固定原文引用

- `KS-MARKS-RISK`；版本`91018cf8e1432cbc923b550004e05ebc8490384a6d9b59002ad4bb80df243351`；字符区间`[10728, 11688)`。

Illiquidity – If an investor needs money with which to pay for surgery in three months or buy a home in a year, he may be unable to make an investment that can’t be counted on for liquidity that meets his schedule. Thus, for him, risk isn’t just losing money or volatility, or any of the above. It’s being unable when needed to turn an investment into cash at a reasonable price. This, too, is a personal risk. Theoretically, a fund whose life is perpetual and whose liquidity needs are predictable shouldn’t be sensitive to this risk and thus should be able to bear it for profit. The bottom line is that investment risk comes in many forms. Many risks matter to some investors but not to others, and they may make a given investment seem safe for some investors but risky for others. Rejecting risk as synonymous with volatility, as I do, eliminates the one measure of risk that’s entirely quantifiable, objective and absolute. This, in turn, makes it hard t

## 13. 更高预期回报不等于实际收益更高

- 规则ID：`KR-RISK-EXPECTED-RETURN`；类型：风险；适用周期：SWING／MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`48d29c9c6ebc9aaeaaa34b99047385ec1f8561139009f3fa71d5f6c37aba8c71`。
- [完整原始规则JSON](rules/KR-RISK-EXPECTED-RETURN.json)

### 来源主张

Marks强调风险投资需要提供更高的预期或承诺回报来吸引资本，但更高预期收益不一定实现，结果分布更宽。

### 系统规则

遇到高回报机会时同时列出亏损机制与不利情景，不把承受更高风险解释为最终收益必然增加。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 收益假设
- 亏损机制
- 不利情景

### 适用条件

- 预期回报属于假设，不能用历史涨幅或高票息替代完整风险分析。

### 分析步骤

- 遇到高回报机会时同时列出亏损机制与不利情景，不把承受更高风险解释为最终收益必然增加。

### 反例与失效环境

- 高杠杆放大上行预期，也可能让一次不利变化造成无法承受的损失。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：风险、预期收益、杠杆、回撤控制。冲突分组：风险与容量。

### 固定原文引用

- `KS-MARKS-RISK`；版本`91018cf8e1432cbc923b550004e05ebc8490384a6d9b59002ad4bb80df243351`；字符区间`[2874, 3924)`。

riskier investments have to offer the prospect of higher returns, or higher promised returns, or higher expected returns. But there’s absolutely nothing to say those higher prospective returns have to materialize. The way I conceptualize the capital market line makes it easier for me to relate to the relationship underlying it all: Risk Return 0 Risk Return 0 2 © Oaktree Capital Management, L.P. All Rights Reserved Riskier investme nts are those where the outcome is less certain. That is, the probability distribution of returns is wider. When priced fairly, riskier investments should entail:  higher expected returns,  the possibility of lower returns, and  in some cases the possibility of losses. The traditional graph shown first above is deceptive, because it communicates the positive connection between risk and return but fails to suggest the uncertainty involved. It has brought a lot of people a lot of misery through its unwavering intimation that taking more risk leads to making more money. I hope my version of the graph is mor

## 14. 跨市场因子表现仍可能暴露共同流动性风险

- 规则ID：`KR-FACTOR-FUNDING-RISK`；类型：市场环境；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`88289431906874bb46255ae80bb8b9702a2df051fa61e50a3587a6cf6117ae85`。
- [完整原始规则JSON](rules/KR-FACTOR-FUNDING-RISK.json)

### 来源主张

AQR摘要指出全球融资流动性风险是价值与动量模式的部分来源，并强调跨市场联合观察。

### 系统规则

使用跨市场因子经验时列出融资环境与样本差异；把流动性影响作为情景假说而非当前资金流事实。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 融资环境证据
- 因子同窗样本
- 市场差异

### 适用条件

- 研究摘要的解释不能替代实时融资指标或完整论文复现，也不证明适用于所有市场。

### 分析步骤

- 使用跨市场因子经验时列出融资环境与样本差异；把流动性影响作为情景假说而非当前资金流事实。

### 反例与失效环境

- 本地交易限制或市场结构差异可能使国外因子结果失效，单期流入不能证实全球机制。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：因子投资、市场环境、融资流动性、跨市场。冲突分组：因子研究。

### 固定原文引用

- `KS-AQR-VALUE-MOMENTUM`；版本`d967a68ed16627dec0f5a7f7a3c6914540e0e88789ed2459c2eb3377636d562f`；字符区间`[3381, 3981)`。

Global funding liquidity risk is a partial source of these patterns, which are identifiable only when examining value and momentum jointly across markets. Our findings present a challenge to existing behavioral, institutional and rational asset pricing theories that largely focus on U.S. equities. The strong correlation structure among value and momentum strategies across such diverse asset classes is difficult to reconcile under existing behavioral theories, for example. The high return premium and Sharpe ratio of a global across-asset-class diversified value and momentum portfolio presents a

## 15. 价值与动量互补只作为待验证组合假说

- 规则ID：`KR-VALUE-MOMENTUM-COMBINATION`；类型：持有；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`6800c1f47e81ac05b5796791514fd4ef5e462caa8c475960a8e417c4f10035b0`。
- [完整原始规则JSON](rules/KR-VALUE-MOMENTUM-COMBINATION.json)

### 来源主张

AQR论文摘要报告多个市场资产类别中的价值与动量收益溢价及二者负相关特征。

### 系统规则

可将价值与动量作为组合研究的不同维度，明确主路线和辅助信号，不把历史相关性视为实时对冲保证。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 价值因子定义
- 动量窗口
- 组合收益与相关性

### 适用条件

- 仅取得官方论文摘要，相关性及组合收益必须在本地同窗样本重新验证。

### 分析步骤

- 可将价值与动量作为组合研究的不同维度，明确主路线和辅助信号，不把历史相关性视为实时对冲保证。

### 反例与失效环境

- 压力期相关性会改变，个股同时便宜且上涨也不等于组合因子互补效果已实现。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：因子投资、价值、动量、组合、相关性。冲突分组：因子研究。

### 固定原文引用

- `KS-AQR-VALUE-MOMENTUM`；版本`d967a68ed16627dec0f5a7f7a3c6914540e0e88789ed2459c2eb3377636d562f`；字符区间`[2764, 3384)`。

We find consistent value and momentum return premia across eight diverse markets and asset classes, and a strong common factor structure among their returns. Value and momentum returns correlate more strongly across asset classes than passive exposures to the asset classes, but value and momentum are negatively correlated with each other, both within and across asset classes. Our results indicate the presence of common global risks that we characterize with a three-factor model that describes a new set of 48 global across-asset-class test assets, the Fama–French portfolios and a variety of hedge fund indices. Glo

## 16. 投资因子需区分资本扩张与实际价值创造

- 规则ID：`KR-FACTOR-INVESTMENT`；类型：筛选；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`0daffacbb44f370bb7e41cb39d580eac470d00b1d29a310653c17aad9b4482b0`。
- [完整原始规则JSON](rules/KR-FACTOR-INVESTMENT.json)

### 来源主张

Fama–French资料将投资因子定义为保守投资组合与激进投资组合的平均收益差。

### 系统规则

研究资本扩张强度时先统一投资口径和样本定义，不把低投资或高投资本身写成必然买卖理由。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 资本投资指标
- 组合构造
- 收益样本

### 适用条件

- 来源是因子定义，未提供当前A股个股投资强度的最佳阈值或有效性证明。

### 分析步骤

- 研究资本扩张强度时先统一投资口径和样本定义，不把低投资或高投资本身写成必然买卖理由。

### 反例与失效环境

- 高回报的新项目可能需要大量投入，低投资也可能来自企业缺少机会或衰退。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：因子投资、投资因子、资本扩张、CMA。冲突分组：因子研究。

### 固定原文引用

- `KS-FRENCH-5F`；版本`3eafb817b1e536b08cfab80b0ccfc19bb32f8fbfcf52e9b45b757d7adca19ba7`；字符区间`[1462, 1777)`。

CMA (Conservative Minus Aggressive) is the average return on the two conservative investment portfolios minus the average return on the two aggressive investment portfolios, CMA = 1/2 (Small Conservative + Big Conservative) - 1/2 (Small Aggressive + Big Aggressive). R m -R f , the excess return on the market, valu

## 17. 盈利因子是组合定义，不能直接变成单股保证

- 规则ID：`KR-FACTOR-PROFITABILITY`；类型：筛选；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`cacd5d520259b4e501df5a3f9cc1b4bf06f4a1520c550e0eef49a7736d8e033f`。
- [完整原始规则JSON](rules/KR-FACTOR-PROFITABILITY.json)

### 来源主张

Fama–French资料把盈利因子定义为强经营盈利组合与弱经营盈利组合的平均收益之差。

### 系统规则

把盈利质量作为候选研究维度，先明确组合构造和盈利指标定义；不能以一个高盈利公司代替完整因子组合。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 经营盈利指标
- 组合样本
- 同窗收益

### 适用条件

- 当前是因子定义参考，尚未具备本地完整历史组合与A股收益验证。

### 分析步骤

- 把盈利质量作为候选研究维度，先明确组合构造和盈利指标定义；不能以一个高盈利公司代替完整因子组合。

### 反例与失效环境

- 单一高盈利股票仍可能价格过贵，不能继承来源样本组合的历史收益。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：因子投资、盈利因子、质量、RMW。冲突分组：因子研究。

### 固定原文引用

- `KS-FRENCH-5F`；版本`3eafb817b1e536b08cfab80b0ccfc19bb32f8fbfcf52e9b45b757d7adca19ba7`；字符区间`[1217, 1464)`。

RMW (Robust Minus Weak) is the average return on the two robust operating profitability portfolios minus the average return on the two weak operating profitability portfolios, RMW = 1/2 (Small Robust + Big Robust) - 1/2 (Small Weak + Big Weak). CM

## 18. 困境与周期企业需要调整持续经营假设

- 规则ID：`KR-DCF-DISTRESS`；类型：风险；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`56508280b01283466e14a8c7d84d86c6d6972c7ab9e7569c0130e0aadea14877`。
- [完整原始规则JSON](rules/KR-DCF-DISTRESS.json)

### 来源主张

Damodaran指出困境企业有破产风险，通常持续经营折现框架不易直接适用；周期企业现金流应考虑经济波动。

### 系统规则

遇到亏损、偿债压力或周期低谷，先区分持续经营、重整和恢复情景，不把单期亏损外推为永续现金流。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 偿债能力
- 经营现金流
- 周期位置
- 持续经营披露

### 适用条件

- 困境与普通周期低谷需区别，缺少恢复或失败证据时保留多情景，不编造恢复概率。

### 分析步骤

- 遇到亏损、偿债压力或周期低谷，先区分持续经营、重整和恢复情景，不把单期亏损外推为永续现金流。

### 反例与失效环境

- 把濒临失败企业当作正常周期反转会高估价值；把暂时低谷永久化也会误判。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：困境、周期、估值风险、负利润。冲突分组：基本面估值。

### 固定原文引用

- `KS-DAMODARAN-VALUATION`；版本`24de32735013499834e0ec2b720427be1192d357d2024fcb5e9fb62a088b1c69`；字符区间`[14297, 15547)`。

(1) Firms in trouble: A distressed firm generally has negative earnings and cashflows. It expects to lose money for some time in the future. For these firms, estimating future cashflows is difficult to do, since there is a strong probability of bankruptcy. For firms which are expected to fail, discounted cashflow valuation does not work very well, since we value the firm as a going concern providing positive cashflows to its investors. Even for firms that are expected to survive, cashflows will have to be estimated until they turn positive, since obtaining a present value of negative cashflows will yield a negative1 value for equity or the firm. (2) Cyclical Firms: The earnings and cashflows of cyclical firms tend to follow the economy - rising during economic booms and falling during recessions. If discounted cashflow valuation is used on these firms, expected future cashflows are usually smoothed out, unless the analyst wants to undertake the onerous task of predicting the timing and duration of economic recessions and recoveries. Many cyclical firms, in the depths of a recession, look like troubled firms, with negative earnings and cashflows. Estimating future cashflows then becomes entangled with analyst predictions about when

## 19. 同一行业并不自动构成估值可比样本

- 规则ID：`KR-RELATIVE-COMPARABILITY`；类型：估值；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`45fd737529015b7e9580302eddd81717051f7ca08f51b2aaba93048f9af270a0`。
- [完整原始规则JSON](rules/KR-RELATIVE-COMPARABILITY.json)

### 来源主张

Damodaran指出相对估值用共同变量标准化可比资产价格，行业平均市盈率隐含企业可比且同行定价正确等前提。

### 系统规则

使用估值倍数前列明业务、增长、风险及会计口径可比性，并检查整个比较组可能共同高估的风险。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 可比公司样本
- 估值倍数
- 增长与风险差异

### 适用条件

- 样本必须有同一时点和统一口径；负利润企业不能机械使用市盈率。

### 分析步骤

- 使用估值倍数前列明业务、增长、风险及会计口径可比性，并检查整个比较组可能共同高估的风险。

### 反例与失效环境

- 同行整体高估或业务结构差异较大时，低于行业平均倍数仍可能不便宜。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：相对估值、可比公司、市盈率、行业估值。冲突分组：基本面估值。

### 固定原文引用

- `KS-DAMODARAN-VALUATION`；版本`24de32735013499834e0ec2b720427be1192d357d2024fcb5e9fb62a088b1c69`；字符区间`[20474, 21194)`。

In relative valuation, the value of an asset is derived from the pricing of 'comparable' assets, standardized using a common variable such as earnings, cashflows, book value or revenues. One illustration of this approach is the use of an industry-average price-earnings ratio to value a firm. This assumes that the other firms in the industry are comparable to the firm being valued and that the market, on average, prices these firms correctly. Another multiple in wide use is the price to book value ratio, with firms selling at a discount on book value, relative to comparable firms, being considered undervalued. The multiple of price to sales is also used to value firms, with the average price-sales ratios of firm

## 20. 现金流、期限与风险共同决定折现估值

- 规则ID：`KR-DCF-DRIVERS`；类型：估值；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`b6b10d1808fa6b6ce449249658b11ec172a80f7c2dd73820b4cd20e4a29a69d4`。
- [完整原始规则JSON](rules/KR-DCF-DRIVERS.json)

### 来源主张

Damodaran说明资产现金流不同，折现率随预期现金流风险变化，风险越高要求的折现率通常越高。

### 系统规则

估值需分别记录现金流预测、期限和折现风险假设，对输入变化进行情景比较，不能只输出一个目标价。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 现金流预测
- 预测期限
- 折现率依据

### 适用条件

- 预测现金流不是已发生事实，折现率不能由大模型凭记忆填写。

### 分析步骤

- 估值需分别记录现金流预测、期限和折现风险假设，对输入变化进行情景比较，不能只输出一个目标价。

### 反例与失效环境

- 增长或终值假设稍有变化就可能显著改变估值，精确结果可能掩盖输入不确定性。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：DCF、现金流折现、估值、敏感性。冲突分组：基本面估值。

### 固定原文引用

- `KS-DAMODARAN-VALUATION`；版本`24de32735013499834e0ec2b720427be1192d357d2024fcb5e9fb62a088b1c69`；字符区间`[2710, 3360)`。

r = Discount rate reflecting the riskiness of the estimated cashflows The cashflows will vary from asset to asset -- dividends for stocks, coupons (interest) and the face value for bonds and after-tax cashflows for a real project. The discount rate will be a function of the riskiness of the estimated cashflows, with higher rates for riskier assets and lower rates for safer projects. You can in fact think of discounted cash flow valuation on a continuum. At one end of the spectrum, you have the default-free zero coupon bond, with a guaranteed cash flow in the future. Discounting this cash flow at the riskless rate should yield the value of the

## 21. 突破入场需要量价证据并防止追高

- 规则ID：`KR-TREND-ENTRY-DISCIPLINE`；类型：入场；适用周期：SWING／MEDIUM。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`248671ba9031e1657fabbacbac015769f1dae987e63d6f58d320ad4f6c4d5f18`。
- [完整原始规则JSON](rules/KR-TREND-ENTRY-DISCIPLINE.json)

### 来源主张

IBD清单要求寻找图形的放量突破，并限制购买价格与图形买点的距离。

### 系统规则

趋势研究必须先定义可复算的形态买点与成交量条件，再检查是否追高；不能根据一日上涨宣称有效突破。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 复权价格序列
- 成交量序列
- 形态买点口径

### 适用条件

- 本系统尚未验证形态识别及A股阈值；原始比例只用于解释来源方法。

### 分析步骤

- 趋势研究必须先定义可复算的形态买点与成交量条件，再检查是否追高；不能根据一日上涨宣称有效突破。

### 反例与失效环境

- 短时放量突破可能是虚假信号；跳空与无法成交会使图形买点不能按预期执行。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：入场时机、突破、追高、成交量、买点。冲突分组：趋势入退场。

### 参数及出处

```json
[
  {
    "applicability": "SOURCE_MARKET_ONLY",
    "citation_index": 0,
    "meaning": "IBD原文买入距离参考；不是本系统已验证的A股入场参数。",
    "name": "距图形买点范围",
    "origin": "AUTHOR_ORIGINAL",
    "value": "5%"
  }
]
```

### 固定原文引用

- `KS-IBD-ENTRY`；版本`cb338d8cdc2ce210e1dd32c9c1e18102bfab11e010c3b469cd77803511eb631d`；字符区间`[4270, 4510)`。

Review the stock’s chart and look for a breakout from a chart pattern on high volume. Buy within 5% of the chart pattern’s buy point. 3 How to When you’re considering buying a stock, follow this THREE-STEP ROUTINE to put the odds of success

## 22. 增长清单保留原始市场与阈值出处

- 规则ID：`KR-TREND-GROWTH-SCREEN`；类型：筛选；适用周期：SWING／MEDIUM。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`4f9b676acc26b34b12a187e09f1d41fb5f830b32d0af9ad82f7fa20939fc39ac`。
- [完整原始规则JSON](rules/KR-TREND-GROWTH-SCREEN.json)

### 来源主张

IBD清单同时观察近期盈利增长、多年盈利增长、销售增长及股东权益回报，而非仅看价格图形。

### 系统规则

将增长与盈利质量列为趋势候选的研究输入；作者数值仅保留为来源市场的参考条件，A股不直接套用。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 季度盈利增长
- 多年盈利增长
- 季度销售增长
- ROE

### 适用条件

- 财务增长须处理低基数、负利润与口径差异；这些原始阈值没有A股效果证明。

### 分析步骤

- 将增长与盈利质量列为趋势候选的研究输入；作者数值仅保留为来源市场的参考条件，A股不直接套用。

### 反例与失效环境

- 一次性收益或低基数可令增长率达标但业务未改善，高ROE也可能来自过高杠杆。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：成长、盈利增长、质量、ROE、趋势。冲突分组：趋势入退场。

### 参数及出处

```json
[
  {
    "applicability": "SOURCE_MARKET_ONLY",
    "citation_index": 0,
    "meaning": "IBD原文最近季度盈利增长筛选参考，非A股准入条件。",
    "name": "最近季度盈利增长",
    "origin": "AUTHOR_ORIGINAL",
    "value": "25%+"
  },
  {
    "applicability": "SOURCE_MARKET_ONLY",
    "citation_index": 0,
    "meaning": "IBD原文ROE参考，非A股统一质量标准。",
    "name": "股东权益回报",
    "origin": "AUTHOR_ORIGINAL",
    "value": "17%+"
  }
]
```

### 固定原文引用

- `KS-IBD-ENTRY`；版本`cb338d8cdc2ce210e1dd32c9c1e18102bfab11e010c3b469cd77803511eb631d`；字符区间`[2166, 2446)`。

25%+ earnings growth in the most recent quarter 25%+ earnings growth over the last 3 years 25%+ sales growth in the most recent quarter 17%+ return on equity (ROE) A new product or service that is leading or revolutionizing its industry That sounds like a lot of research. Is ther

## 23. 趋势入场先检查整体市场环境

- 规则ID：`KR-TREND-MARKET`；类型：市场环境；适用周期：SWING／MEDIUM。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`3cdfa7d12bc6cbaffda9ae9d16761b89f236046b837beadf149030514cc36be9`。
- [完整原始规则JSON](rules/KR-TREND-MARKET.json)

### 来源主张

IBD买入清单先检查其市场暴露指标，再检查个股基本面及放量突破，并要求组合与市场条件匹配。

### 系统规则

A股趋势研究先检查有真实数据的市场状态，再评估个股信号；不把IBD专有指标冒充本地已实现指标。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 市场行情
- 个股财务
- 复权价格
- 成交量

### 适用条件

- IBD市场暴露指标和美国经验不是本地市场择时模型，A股判定口径待验证。

### 分析步骤

- A股趋势研究先检查有真实数据的市场状态，再评估个股信号；不把IBD专有指标冒充本地已实现指标。

### 反例与失效环境

- 市场总体转弱时个股突破可能失败；总体走强也不能替代个股条件核查。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：市场环境、趋势、入场时机、仓位。冲突分组：趋势入退场。

### 固定原文引用

- `KS-IBD-ENTRY`；版本`cb338d8cdc2ce210e1dd32c9c1e18102bfab11e010c3b469cd77803511eb631d`；字符区间`[4022, 4802)`。

Check the Stock Market Exposure indicator on investors.com to make sure buying a new position will keep your portfolio aligned with the current market conditions. Go to the IBD Stock Checkup and make sure a stock’s fundamentals get good ratings. 2 Review the stock’s chart and look for a breakout from a chart pattern on high volume. Buy within 5% of the chart pattern’s buy point. 3 How to When you’re considering buying a stock, follow this THREE-STEP ROUTINE to put the odds of success in your favor. BUY STOCKS 0%-20%: The most cautious level. Put very little, if any, of your investing capital at risk. 20%-40%: Remain cautious, with your portfolio tilted toward cash. But you might try out some of your stock ideas when the market starts to improve. If the market is pulling

## 24. 退出理由同时考虑价值、替代机会和成本

- 规则ID：`KR-EXPECTATION-EXIT`；类型：退出；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`12c95f541f54aa0826b19edf78952dca28eb9e7c8f7552664fa85918527ce544`。
- [完整原始规则JSON](rules/KR-EXPECTATION-EXIT.json)

### 来源主张

预期投资列出达到预期价值、更好机会和预期下调等卖出理由，并提示税与交易成本可能改变决策。

### 系统规则

定期复核原持有依据、估值和可比机会，列清退出触发条件及成本，不能只因上涨或下跌机械换股。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 当前估值
- 原始持有依据
- 替代机会
- 交易成本

### 适用条件

- 与基本面路线一致；当前未计算A股税费和交易数量，不能生成直接交易指令。

### 分析步骤

- 定期复核原持有依据、估值和可比机会，列清退出触发条件及成本，不能只因上涨或下跌机械换股。

### 反例与失效环境

- 更好机会的预期不可靠或换手成本较高时，频繁替换可能降低实际收益。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：退出、卖出、预期下调、交易成本。冲突分组：基本面估值。

### 固定原文引用

- `KS-EXPECTATIONS`；版本`11abb69f49ea2df66b47171fdbc97fd1adf3fef5576f7922816a2eee9aeb3c5b`；字符区间`[3749, 4289)`。

Sell stocks that trade at sufficient premiums over expected value The higher a stock price’s premium to its expected value, the more compelling the selling opportunity. Investors should sell a stock for three reasons: it has reached its expected value, better investment opportunities exist, or the investor revises expectations downward. But even these reasons may not be decisive after incorporating taxes and transactions costs into the analysis. Don’t overlook value determinants missing from the financial statements For example, compa

## 25. 用竞争变化核查预期修正机制

- 规则ID：`KR-EXPECTATION-REVISION`；类型：筛选；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`82970f183ac8fb92b4f628bf0226f4971e972368506e38e573b1045c1c4dc1c3`。
- [完整原始规则JSON](rules/KR-EXPECTATION-REVISION.json)

### 来源主张

预期投资将竞争战略与财务分析结合，用竞争动态变化寻找预期可能修正的方向。

### 系统规则

围绕竞争份额、产品和盈利机制核查预期差，区分已披露变化与尚未发生的催化假说。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 竞争披露
- 财务披露
- 预期假设

### 适用条件

- 必须说明竞争变化如何传导到现金流，而不只是罗列热门题材。

### 分析步骤

- 围绕竞争份额、产品和盈利机制核查预期差，区分已披露变化与尚未发生的催化假说。

### 反例与失效环境

- 竞争优势改善若已被价格充分计入，经营利好也可能没有超额回报。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：竞争优势、预期修正、成长、催化。冲突分组：基本面估值。

### 固定原文引用

- `KS-EXPECTATIONS`；版本`11abb69f49ea2df66b47171fdbc97fd1adf3fef5576f7922816a2eee9aeb3c5b`；字符区间`[3075, 3775)`。

Use competitive strategy analysis to help anticipate revisions The surest way for investors to anticipate expectations revisions is to foresee shifts in a company’s competitive dynamics. For investors, competitive strategy analysis integrated with financial analysis is an essential tool in the expectations game. Buy stocks that trade at sufficient discounts from expected value The greater the discount from expected value, the higher the prospective excess return—and hence the more attractive a stock is for purchase. The sooner the stock price converges toward the higher expected value, the greater the excess return. The longer it takes, the lower the excess return. Sell stocks that trade at 

## 26. 先反推价格隐含的经营预期

- 规则ID：`KR-PRICE-EXPECTATIONS`；类型：估值；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`46836c3386c3991c287faf07c4aa35fad4ef671f2c2cb79f6597b2e893b798f3`。
- [完整原始规则JSON](rules/KR-PRICE-EXPECTATIONS.json)

### 来源主张

预期投资先读取当前价格隐含的现金流预期，再判断预期修正机会并作买卖持有决策。

### 系统规则

把价格隐含的增长和现金流要求，与有来源的经营情景比较；显式保留反推模型假设。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 真实价格
- 现金流估值模型
- 经营情景

### 适用条件

- 只采用取得的公开方法介绍，不能声称已覆盖整本书或精确预测明日价格。

### 分析步骤

- 把价格隐含的增长和现金流要求，与有来源的经营情景比较；显式保留反推模型假设。

### 反例与失效环境

- 相同价格可由不同增长与折现假设解释；反推得到单个参数不代表市场只有一种预期。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：预期差、价格隐含预期、现金流、估值。冲突分组：基本面估值。

### 固定原文引用

- `KS-EXPECTATIONS`；版本`11abb69f49ea2df66b47171fdbc97fd1adf3fef5576f7922816a2eee9aeb3c5b`；字符区间`[5825, 6475)`。

Estimate Price-Implied Expectations. The expectations investor “reads” the expectations for cash flow embedded in a company's current stock price. Identify Expectations Opportunities. The expectations investor then assesses those expectations, evaluates the company's competitive position, and considers the likelihood of upward or downward revisions in those expectations. Buy, Sell, or Hold? The expectations investor makes a buy, sell, or hold decision, making sure that all investments have a clear-cut after-tax “margin of safety” between the stock's price today and the expected price tomorrow. Do I need to be a financial guru to understand an

## 27. 估值略高于价格仍不构成安全边际

- 规则ID：`KR-MARGIN-SAFETY`；类型：入场；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`9e8c00628379ddb3e5d69807c771ecbfe53d678c917a1fee9c7a43ab83852fd8`。
- [完整原始规则JSON](rules/KR-MARGIN-SAFETY.json)

### 来源主张

巴菲特要求购买价格留有安全边际，估计价值仅略高于价格时并无购买兴趣。

### 系统规则

对估值关键假设做不利情景检查，再讨论价格与价值差异，不能把估值点值直接视为买点。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 真实价格
- 估值假设
- 不利情景

### 适用条件

- 估值依据和误差区间必须可解释，原文未给统一安全边际百分比。

### 分析步骤

- 对估值关键假设做不利情景检查，再讨论价格与价值差异，不能把估值点值直接视为买点。

### 反例与失效环境

- 估值假设系统性过于乐观时，表面折价也不能提供真实保护。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：安全边际、估值、入场时机、买点。冲突分组：基本面估值。

### 固定原文引用

- `KS-BUFFETT-1992`；版本`b3c247bf017230ee44b6395a07f1f5ed4ff00c38b055422df5480e420f9a00ab`；字符区间`[39396, 39826)`。

Second, and equally important, we insist on a margin of safety in our purchase price. If we calculate the value of a common stock to be only slightly higher than its price, we're not interested in buying. We believe this margin-of-safety principle, so strongly emphasized by Ben Graham, to be the cornerstone of investment success. Fixed-Income Securities Below we list our largest holdings of fixed-income securities: (000s omitt

## 28. 现金流难以解释时缩小结论范围

- 规则ID：`KR-CIRCLE-COMPETENCE`；类型：筛选；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`816e17251c89f70dc8c9098f88661ef3a98f157a70c66c798db707c380b03ab0`。
- [完整原始规则JSON](rules/KR-CIRCLE-COMPETENCE.json)

### 来源主张

巴菲特倾向简单稳定、自己能理解的业务，并承认复杂多变业务的未来现金流难以预测。

### 系统规则

先说明收入来源、成本和竞争变化；无法解释关键现金流驱动时标记分析边界，避免精确估值。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 业务结构
- 现金流驱动
- 行业变化

### 适用条件

- 能力边界来自业务解释与资料核验，不以熟悉公司名字替代。

### 分析步骤

- 先说明收入来源、成本和竞争变化；无法解释关键现金流驱动时标记分析边界，避免精确估值。

### 反例与失效环境

- 看似稳定的旧业务遭遇技术替代时，过去经验可能无法解释未来现金流。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：能力圈、业务理解、现金流、筛选。冲突分组：基本面估值。

### 固定原文引用

- `KS-BUFFETT-1992`；版本`b3c247bf017230ee44b6395a07f1f5ed4ff00c38b055422df5480e420f9a00ab`；字符区间`[38888, 39568)`。

First, we try to stick to businesses we believe we understand. That means they must be relatively simple and stable in character. If a business is complex or subject to constant change, we're not smart enough to predict future cash flows. Incidentally, that shortcoming doesn't bother us. What counts for most people in investing is not how much they know, but rather how realistically they define what they don't know. An investor needs to do very few things right as long as he or she avoids big mistakes. Second, and equally important, we insist on a margin of safety in our purchase price. If we calculate the value of a common stock to be only slightly higher than its price,

## 29. 增长要与增量资本回报一起判断

- 规则ID：`KR-GROWTH-RETURN`；类型：持有；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`955b2c3550391b916e9c886a2fb786d2fccb883dba9529cdc4fec4896f0f0452`。
- [完整原始规则JSON](rules/KR-GROWTH-RETURN.json)

### 来源主张

巴菲特把可长期以高回报运用大量增量资本的企业，与持续投入更多资本却低回报的企业相对照。

### 系统规则

持续跟踪增长是否伴随有质量的资本回报；增长消耗资本且回报恶化时重新评估持有逻辑。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 增量资本投入
- 经营收益
- 股东分配
- 估值

### 适用条件

- 明确增量资本与回报的期间及业务口径，仍需单独考虑买入价格。

### 分析步骤

- 持续跟踪增长是否伴随有质量的资本回报；增长消耗资本且回报恶化时重新评估持有逻辑。

### 反例与失效环境

- 高增长由低回报扩张支撑时会损害价值；历史高回报也不保证新增项目延续。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：成长、ROIC、资本回报、持有、再投资。冲突分组：基本面估值。

### 固定原文引用

- `KS-BUFFETT-1992`；版本`b3c247bf017230ee44b6395a07f1f5ed4ff00c38b055422df5480e420f9a00ab`；字符区间`[38023, 39003)`。

Leaving the question of price aside, the best business to own is one that over an extended period can employ large amounts of incremental capital at very high rates of return. The worst business to own is one that must, or will , do the opposite - that is, consistently employ ever-greater amounts of capital at very low rates of return. Unfortunately, the first type of business is very hard to find: Most high-return businesses need relatively little capital. Shareholders of such a business usually will benefit if it pays out most of its earnings in dividends or makes significant stock repurchases. Though the mathematical calculations required to evaluate equities are not difficult, an analyst - even one who is experienced and intelligent - can easily go wrong in estimating future "coupons." At Berkshire, we attempt to deal with this problem in two ways. First, we try to stick to businesses we believe we understand. That means they must be relatively simple and stable

## 30. 先看企业经济特征，再看便宜程度

- 规则ID：`KR-QUALITY-ECONOMICS`；类型：筛选；适用周期：MEDIUM／LONG。
- 状态：`PUBLISHED`／`REFERENCE_ONLY`／`UNVALIDATED`。
- 固定版本：`c7e6f2b0df8e10ac42adc6811810943fc611c6d7b2d1d6b255bf6865877bdec9`。
- [完整原始规则JSON](rules/KR-QUALITY-ECONOMICS.json)

### 来源主张

巴菲特强调合理价格的优秀企业，并用经营困难的企业说明优秀管理不能轻易扭转恶劣经济特征。

### 系统规则

研究低估值股票时同时检查业务回报、竞争优势与资本消耗，不能只凭低价格判定便宜。

### A股适配说明

系统将原文转为研究检查项；A股指标口径、交易约束及效果仍需单独核验，当前不能自动执行。

### 需要的数据

- 业务披露
- 盈利与资本投入
- 估值口径

### 适用条件

- 必须有支持业务经济特征的具体披露，而非只引用管理层口号。

### 分析步骤

- 研究低估值股票时同时检查业务回报、竞争优势与资本消耗，不能只凭低价格判定便宜。

### 反例与失效环境

- 行业经济特征持续恶化时，即使管理者优秀且估值低，也可能形成价值陷阱。

### 禁止推断

- 不得据此推出下一交易日涨跌、保证收益或生成未经工具核算的交易数量。

### 关键词与冲突分组

关键词：质量、价值陷阱、低估值、企业经济特征。冲突分组：基本面估值。

### 固定原文引用

- `KS-BUFFETT-1989`；版本`bfc0b19e51fb7ce0952ef34c221ce5801bdc79b1809f18d239316a443fa0ec66`；字符区间`[72874, 73844)`。

It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price. Charlie understood this early; I was a slow learner. But now, when buying companies or common stocks, we look for first-class businesses accompanied by first- class managements. o That leads right into a related lesson: Good jockeys will do well on good horses, but not on broken-down nags. Both Berkshire's textile business and Hochschild, Kohn had able and honest people running them. The same managers employed in a business with good economic characteristics would have achieved fine records. But they were never going to make any progress while running in quicksand. I've said many times that when a management with a reputation for brilliance tackles a business with a reputation for bad economics, it is the reputation of the business that remains intact. I just wish I hadn't been so energetic in creating examples. My behavior has matched that admitted by Mae 
