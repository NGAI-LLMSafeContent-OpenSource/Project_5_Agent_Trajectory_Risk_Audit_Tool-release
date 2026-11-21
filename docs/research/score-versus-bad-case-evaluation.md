# 总分与坏例：高分是否等于系统更好

> 日期：2026-11-12  
> 问题：主流 ML / LLM / agent-safety 评测里，更高的汇总分是否表示系统更好？逐条错误分析（坏例）是否被认为比总分更适合用来改进系统？本地项目对 12 条冻结轨迹分别打 verdict、localization、evidence，并写出定性坏例卡片。怀疑者认为高分不能说明 Topic 4 的算法好，低分也不能说明它坏；具体坏例更扎实。

## 结论

这些一手来源不支持两句极端话。更高的单一总分不是“算法好”的通用证明；逐条坏例也不是比分数更有效、因而可以取代分数的裁决。

1. 单一更高的数字只在“这个指标、这个集合”上表示更好。HELM 拒绝用一个 accuracy 给模型排全序，并认为不存在满足所有偏好的通用聚合。NIST AI RMF 的 Measure 明确包含定量、定性和混合方法，并警告指标会被简化、被博弈、丢掉关键差别。
2. Andrew Ng 在已经选定的单一指标上，确实用更高的分数判断分类器 A 优于 B。同一章体系又写明：若该排序和产品判断相反，就不能再信任这个指标，应当改指标或改数据集，而不是放弃指标、改由人工挑模型。
3. 看错例是改进时的标准步骤，用来找出原因、估计某项修改最多能把分数提高多少。Google Rule #26 和 Ng 的 error analysis 都是这个用法。Google Rule #27 要求先把不满意的行为量化，再优化。
4. Agent 安全评测把“是否检出”和“能否定位 / 诊断”分开报告。HINTBench 上检测 Avg-F1 可以超过 80，细粒度 Strict-F1 最好的模型仍低于 37。ATBench 上二元 F1 最高的模型，不是三维诊断最好的模型。
5. 对这 12 条冻结轨迹，分数回答的是：在已定义的 verdict、localization、evidence 上，这 12 条差多少。坏例卡片回答的是：错在哪、哪一类错误值得改。Ng 把 100 条开发集已经称为偏小，常见规模是 1,000 到 10,000。12 条更接近一份错误分析样本，而不是这些文献用来给算法下总评的开发集。

## 一手资料发现

### 1. HELM：一个模型得到的是分数矩阵，不是一个总分

Liang 等人的 HELM 把评测定义成多指标，而不是一个 accuracy。[Holistic Evaluation of Language Models, arXiv:2211.09110](https://arxiv.org/abs/2211.09110)，正文使用 [HTML 版](https://arxiv.org/html/2211.09110)。

摘要把做法写成同时测量 7 类指标，并让模型之间、指标之间的取舍露出来：

> Second, we adopt a multi-metric approach: We measure 7 metrics (accuracy, calibration, robustness, fairness, bias, toxicity, and efficiency) for each of 16 core scenarios to the extent possible (87.5% of the time), ensuring that metrics beyond accuracy don’t fall to the wayside, and that trade-offs across models and metrics are clearly exposed.

第 1 节把 holistic evaluation 的第二项写成：

> Multi-metric measurement. Societally beneficial systems reflect many values, not just accuracy. Holistic evaluation should represent these plural desiderata, evaluating every desideratum for each scenario considered.

第 9 节对比了给模型一个分数、一个分数向量、一个分数矩阵：

> More simply, our concrete benchmark differs from both traditional benchmarks like ImageNet that assign a single score (i.e. the ImageNet accuracy) and meta-benchmarks like GLUE that assign a score vector (i.e. the accuracies on the GLUE datasets) to each model. Instead, we assign a score matrix to each model: for each use case, we report scores across several desiderata (e.g. accuracy, calibration, robustness, fairness, efficiency).

第 11.3 节直接讨论能不能聚成一个数。他们的结论是：不能靠 accuracy 得到全序；单一数字有用，但是还原性的，而且没有一种通用聚合能覆盖所有偏好：

> For example, in comparison to benchmarks like ImageNet or SQuAD, we cannot simply rank models by accuracy to get a total order: we believe this correctly reflects the trade-offs that different models impose over the evaluation space. In other words, while it is possible for model A to be strictly better on every metric for every scenario than model B (i.e. strict Pareto dominance), in almost cases A is sometimes better and B is sometimes better when one is sufficiently holistic/expansive. To designate A or B as better requires making a judgment that (implicitly/explicitly) weighs the circumstances in which each is better than the other.

> We leave the question of aggregating model performance to a single number (or a single number per scenario, or per metric) to future work. We do not believe there exists a universal aggregation that satisfies all preferences, reflects all values, or captures all circumstances appropriately. However, we do believe single-number metrics, though reductive, are useful practical tools to simplify decision-making.

同一篇论文也要求把数量结果映回具体模型行为，而不是只看表。第 8 节：

> This interface not only provides the quantitative results, as is customary for other benchmarks and leaderboards, but also the underlying model predictions and the exact inputs and prompts that yielded these predictions. We encourage exploration of these results interactively: we believe grounding and interrogating various quantitative trends by mapping them to explicit model behaviors is necessary for the community to have common understanding of these models.

因此 HELM 的立场是：多指标矩阵和具体预测都要保留；单一更高的数字不是“更好”的定义。原文 “in almost cases” 按论文印刷保留。

### 2. NIST AI RMF 1.0：Measure 不是一个汇总分

[NIST AI 100-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf)（AI RMF 1.0）第 1.2.1 节把测不准和风险高低分开，并列出指标本身的失败方式。下列引文按 PDF 第 4–5 页的断词还原；[AIRC 对 Core 的官方摘录](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)与 PDF 第 5.3 节一致。

> AI risks or failures that are not well-defined or adequately understood are difficult to measure quantitatively or qualitatively. The inability to appropriately measure AI risks does not imply that an AI system necessarily poses either a high or low risk.

> In addition, measurement approaches can be oversimplified, gamed, lack critical nuance, become relied upon in unexpected ways, or fail to account for differences in affected groups and contexts.

第 5.3 节 Measure 的第一句把测量方法写成三类，不是一个分数：

> The measure function employs quantitative, qualitative, or mixed-method tools, techniques, and methodologies to analyze, assess, benchmark, and monitor AI risk and related impacts.

完成 Measure 之后，文件要求考虑每一种测量各自提供了什么信息，并允许发展新的定性和定量测量：

> New types of measurement, qualitative and quantitative, may need to be developed. The degree to which each measurement type provides unique and meaningful information to the assessment of AI risks should be considered.

子类把“错误报告”和“定性或定量”写进测量本身：

> Measure 1.2: Appropriateness of AI metrics and effectiveness of existing controls are regularly assessed and updated, including reports of errors and potential impacts on affected communities.

> Measure 2.3: AI system performance or assurance criteria are measured qualitatively or quantitatively and demonstrated for conditions similar to deployment setting(s). Measures are documented.

Measure 2.6 到 2.12 继续把安全、安全韧性、透明度、隐私、公平、环境影响分开测。低分或测不出来，在这份标准里都不是“系统风险低/高”的自动结论。

### 3. HINTBench：检测分高，不表示定位和类型判断也好

[HINTBench, arXiv:2604.13954](https://arxiv.org/abs/2604.13954) 第 4 节，[HTML](https://arxiv.org/html/2604.13954)。论文把审计拆成三个任务，并分开报指标。摘要：

> Experiments reveal a substantial capability gap: strong LLMs perform well on trajectory-level risk detection, but the best model remains below 37 on fine-grained Strict-F1 for risk-step localization.

第 4.1 节：

> We evaluate three tasks: trajectory-level risk detection, coarse risk-step localization with five constraint categories, and fine localization with exact risk subtypes. We report Macro-F1 (Avg-F1), Accuracy, and per-class F1 for detection, and Recall, Localization F1 (Loc-F1), and strict variants requiring the gold primary type for localization. A predicted step matches a gold step within ± 3 steps; thus Loc-F1 ignores type while Strict-F1 measures primary-label diagnosis.

第 4.2 节用同一张表里的数字说明检测不能代表审计能力。Table 3 按 Coarse Loc-F1 排序，不按检测分排序：

> Several strong models achieve Avg-F1 above 80 on risk detection, but their performance drops sharply once step-level localization and type prediction are required. For example, Claude Sonnet 4.6 reaches 80.34 Avg-F1 on risk detection, but only 45.88 and 36.07 Strict-F1 on coarse-grained and fine-grained localization, respectively. This shows that trajectory-level detection does not fully reflect true auditing ability in long-horizon agent settings.

Table 3 中 Claude Opus 4.6 的细粒度 Strict-F1 是 36.74，Sonnet 4.6 是 36.07，与摘要“低于 37”一致。人类参照是 73.20，表注写明不参与排名。

### 4. ATBench：二元指标和三维诊断分列

[ATBench, arXiv:2604.02022](https://arxiv.org/abs/2604.02022)，[HTML](https://arxiv.org/html/2604.02022)。Table 2 的表注把两套结果分开：

> ATBench-C reports coarse-grained binary safe/unsafe classification accuracy, precision, recall, and F1 score. ATBench-F reports fine-grained diagnosis accuracy on Risk Source (R.S.), Failure Mode (F.M.), and Real-world Harm (R.H.) over unsafe trajectories.

第 4.1 节的指标定义：

> For trajectory-level safety evaluation (ATBench-Coarse), we report accuracy, precision, recall, and F1 score of the binary safe/unsafe classification task, where the unsafe class is treated as the positive class. Over the unsafe subset, we additionally report fine-grained diagnosis accuracy (ATBench-Fine-grained) on the three taxonomy dimensions, namely Risk Source (R.S.), Failure Mode (F.M.), and Real-world Harm (R.H.).

第 4.2 节：

> We report results at two levels: coarse-grained binary detection and fine-grained taxonomy diagnosis. A consistent pattern emerges: evaluators can often detect that a trajectory is unsafe, but struggle to diagnose the underlying risk.

> Fine-grained diagnosis is substantially harder than detection. Even strong closed-source models reach only 33.6% on risk source and 13.5% on failure mode, while real-world harm prediction remains limited at 30.2% for GPT-5.4.

Table 2 的具体数字：GPT-5.4 的 coarse F1 是 76.7%，R.S. / F.M. / R.H. 是 33.6 / 13.5 / 30.2。Gemini-3-Flash 的 accuracy 更高（76.4% 对 73.7%），F1 却是 74.9%。AgentDoG-Qwen3-4B 的 coarse F1 是 71.1%，低于 GPT-5.4，细诊断却是表中最高（46.8 / 16.5 / 40.6）。同一张表里，accuracy、F1 和诊断 accuracy 选出的不是同一个“最好”模型。

论文还用单条轨迹说明检测成功不等于诊断成功。Figure 3 图注：

> Representative case studies for failure-mode misidentification (a) and risk-source misattribution (b). In both cases all four evaluators correctly detect the trajectory as unsafe but fail to recover the fine-grained diagnostic label.

### 5. Google Rules of Machine Learning：看错例，同时不要把一个目标当成系统健康

Martin Zinkevich, [Rules of Machine Learning](https://developers.google.com/machine-learning/guides/rules-of-ml)。术语表先把 Metric 和 Objective 分开：

> Metric: A number that you care about. May or may not be directly optimized.

> Objective: A metric that your algorithm is trying to optimize.

“Your First Objective”一节：

> You have many metrics, or measurements about the system that you care about, but your machine learning algorithm will often require a single objective, a number that your algorithm is "trying" to optimize. I distinguish here between objectives and metrics: a metric is any number that your system reports, which may or may not be important.

Rule #2 要求多收集指标：

> By being more liberal about gathering metrics, you can gain a broader picture of your system. Notice a problem? Add a metric to track it!

Rule #12 禁止把正在优化的那一个数当成系统是否健康：

> Don’t take this rule too far though: do not confuse your objective with the ultimate health of the system (see Rule #39).

看具体例子的规则是 Rule #26，标题是 “Look for patterns in the measured errors, and create new features.”：

> Suppose that you see a training example that the model got "wrong". In a classification task, this error could be a false positive or a false negative. In a ranking task, the error could be a pair where a positive was ranked lower than a negative. The most important point is that this is an example that the machine learning system knows it got wrong and would like to fix if given the opportunity.

> Once you have examples that the model got wrong, look for trends that are outside your current feature set.

Rule #24 把定性查看放在定量差异之后，而不是代替它：

> Looking over queries where the symmetric difference is high can help you to understand qualitatively what the change was like.

Rule #27 接着要求把观察到的坏行为变成数字。页面原文在引号后多了一个空格：

> If your issues are measurable, then you can start using them as features, objectives, or metrics. The general rule is " measure first, optimize second".

Rule #39 用一次发布评审说明：直接优化的损失下降、线上某个率上升，仍可能因为另一个指标变差而不发布。

> The logistic loss drops. When she does a live experiment, she sees the install rate increase. However, when she goes to a launch review meeting, someone points out that the number of daily active users drops by 5%. The team decides not to launch the model. Alice is disappointed, but now realizes that launch decisions depend on multiple criteria, only some of which can be directly optimized using ML.

> The only easy launch decisions are when all metrics get better (or at least do not get worse).

> Moreover, no metric covers the team’s ultimate concern, "where is my product going to be five years from now"?

### 6. Machine Learning Yearning：单一数字用来排序，错例用来决定改什么

旧落地页 `https://www.deeplearning.ai/machine-learning-yearning/` 返回 404。本次打开的是 DeepLearning.AI 自己托管的 PDF，不是第三方镜像：[Machine Learning Yearning](https://info.deeplearning.ai/hubfs/andrew-ng-machine-learning-yearning-1.pdf)（封面写明 “Machine Learning Yearning is a deeplearning.ai project. © 2018 Andrew Ng.”，页眉为 Draft）。介绍页是 [info.deeplearning.ai/machine-learning-yearning-book](https://info.deeplearning.ai/machine-learning-yearning-book)。

第 7 章把开发集规模和“能不能看出算法差异”绑在一起。他没有讨论 12 条，但把 100 条已经称为偏小：

> The dev set should be large enough to detect differences between algorithms that you are trying out. For example, if classifier A has an accuracy of 90.0% and classifier B has an accuracy of 90.1%, then a dev set of 100 examples would not be able to detect this 0.1% difference. Compared to other machine learning problems I’ve seen, a 100 example dev set is small. Dev sets with sizes from 1,000 to 10,000 examples are common.

第 8 章确实用更高的单一数字判断优劣，范围限定在这个指标上：

> Classification accuracy is an example of a single-number evaluation metric: You run your classifier on the dev set (or test set), and get back a single number about what fraction of examples it classified correctly. According to this metric, if classifier A obtains 97% accuracy, and classifier B obtains 90% accuracy, then we judge classifier A to be superior.

> Having a single-number evaluation metric speeds up your ability to make a decision when you are selecting among a large number of classifiers. It gives a clear preference ranking among all of them, and therefore a clear direction for progress.

第 9 章补充：多个目标不一定合成一个公式。运行时间可以是 satisficing metric，准确率是 optimizing metric。只要求前者“够好”，再最大化后者。

第 11 章是“高分不等于产品上更好”的直接出处：

> For example, if your dev set + metric ranks classifier A above classifier B, but your team thinks that classifier B is actually superior for your product, then this might be a sign that you need to change your dev/test sets or your evaluation metric.

他举的例子是：A 的 accuracy 更高，但会放过少量色情图片，因此产品上不能接受。

> Here, the metric is failing to identify the fact that Algorithm B is in fact better than Algorithm A for your product. So, you can no longer trust the metric to pick the best algorithm. It is time to change evaluation metrics.

他明确反对丢掉指标、改由人工挑选：

> I would strongly recommend picking a new metric and using the new metric to explicitly define a new goal for the team, rather than proceeding for too long without a trusted metric and reverting to manually choosing among classifiers.

第 14 章定义 error analysis。它是改进方法，不是另一套总评：

> 1. Gather a sample of 100 dev set examples that your system misclassified. I.e., examples that your system made an error on.
> 2. Look at these examples manually, and count what fraction of them are dog images.

> The process of looking at misclassified examples is called error analysis.

> Error Analysis refers to the process of examining dev set examples that your algorithm misclassified, so that you can understand the underlying causes of the errors. This can help you prioritize projects—as in this example—and inspire new directions

数完错例之后，他仍回到准确率会变化多少：

> This simple counting procedure of error analysis gives you a quick way to estimate the possible value of incorporating the 3rd party software for dog images. It provides a quantitative basis on which to decide whether to make this investment.

### 7. NIST AI 600-1：定性反馈和单次事件与指标并列，轶事测试不能代替测量

[NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)（Generative Artificial Intelligence Profile，July 2024，[doi:10.6028/NIST.AI.600-1](https://doi.org/10.6028/NIST.AI.600-1)）在附录 A.1.4 写明，标准化考试式的轶事测试和仅靠基准集，都不能保证部署语境下的有效性：

> For example, the anecdotal testing of GAI system capabilities through video games or standardized tests designed for humans (e.g., intelligence tests, professional licensing exams) does not guarantee GAI system validity or reliability in those domains.

> Measurement gaps can arise from mismatches between laboratory and real-world settings. Current testing approaches often remain focused on laboratory conditions or restricted to benchmark test datasets and in silico techniques that may not extrapolate well to—or directly assess GAI impacts

A.1.5 把结构化的公开反馈放在传统测量旁边，用来校准和核验测量，而不是替换测量：

> Structured public feedback can be used to evaluate whether GAI systems are performing as intended and to calibrate and verify traditional measurement methods.

同一节把红队定义成寻找具体有害输出，并把结果用于调试：

> AI Red-teaming: A structured testing exercise used to probe an AI system to find flaws and vulnerabilities such as inaccurate, harmful, or discriminatory outputs, often in a controlled environment and in collaboration with system developers. [...] Results and insights gleaned from these exercises can serve multiple purposes, including improving data quality and preprocessing, bolstering governance decision making, and enhancing system documentation and debugging practices.

A.1.8 把单个人的一次事件和系统性的聚合事件都当作事件：

> AI incidents can occur in the aggregate (i.e., for systemic discrimination) or acutely (i.e., for one individual).

> Documenting, reporting, and sharing information about GAI incidents can help mitigate and prevent harmful outcomes by assisting relevant AI Actors in tracing impacts to their source.

建议动作里，单条失败和事后复盘被写成持续改进的输入。MS-2.5-001 同时禁止用狭窄的轶事评估外推能力（PDF 表格原文是 “non systematic”）：

> Avoid extrapolating GAI system performance or capabilities from narrow, non systematic, and anecdotal assessments.

MG-4.1-004：

> Implement active learning techniques to identify instances where the model fails or produces unexpected outputs.

MG-4.2-002：

> Conduct post-mortem analyses of incidents with relevant AI Actors, to understand the root causes and implement preventive measures.

## 对这 12 条冻结轨迹意味着什么

本地评分同时有分量（verdict、localization、evidence）和坏例卡片。上面的文献支持保留这两层，不支持用一个总分给 Topic 4 的算法下总评。

- 若把 12 条上的各项合成一个数，再解释成“算法好/坏”，这超出了 HELM、NIST 和 Ng 第 11 章允许的范围。HELM 连大规模、多场景的分数矩阵都不聚成全序。Ng 只在指标和开发集都对准产品目标时，才用更高的数字表示更好。
- 检测分和定位分、诊断分可以反向。HINTBench 和 ATBench 都是在同一批轨迹上观察到的。只报一个汇总分，会把这种差距盖住。
- 坏例卡片对应的是 Ng 第 14 章和 Google Rule #26：看判错的例子，归类，决定下一步改什么。Ng 要求数这些错例，并估计分数的上限变化；他不把“我觉得这几条很糟”当作比指标更有效的总裁决。
- 12 条的规模，Ng 没有直接规定。他写的可比较开发集常见是 1,000 到 10,000，并认为 100 条已经偏小、看不出 0.1% 的差异。因此这 12 条适合作为错误分析样本；它们的高低分只描述这 12 条、这些指标，不能外推成算法在其他分布上的好坏。
