# ADR-0003：冻结课题四 Provider 结果与 transport 边界

- 状态：已接受
- 日期：2026-11-03
- 关联：Issue #4、Issue #5

课题四与课题五只通过文件式 Provider CLI 和 `topic4-audit-result/1` 集成；课题五不 import 课题四内部模块，也不消费其诊断或 visualization sidecar。完整合法的 complete、partial、failed 或 rejected JSON 都是可靠语义结果并统一退出 0，非零退出只表示 transport/进程没有可靠完成；结果必须通过同目录临时文件原子发布。这样会要求课题四修改现有退出码与 rejected 校验，但避免课题五在非零退出后猜测输出是否仍可消费，并确保 partial 和结构化失败不会被当作进程故障丢弃。
