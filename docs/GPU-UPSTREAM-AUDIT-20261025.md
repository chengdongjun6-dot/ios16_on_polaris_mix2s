# 2026-10-25 上游 GPU 更新核对

核对方式：GitHub REST API（gh）、固定提交源码和 Actions 完整日志。时间按北京时间。只下载和审阅，没有合并驱动、运行新 CI、重启或写入手机分区。

## 哪些项目有更新

| 仓库 | 已核对提交 / 日期 | 与本机的关系 |
| --- | --- | --- |
| luuu20309/ios16_on_polaris_mix2s | c9508ae86deb，10 月 23 日 22:46 | 当前本地 origin/main 已是此提交；未发现新的 GPU 更新。仅 main 与旧诊断分支，无打开的 PR。 |
| dipa-studio/ios-sweet | 45263bee51df，10 月 23 日 19:55 | 最近为网站、许可、panic-log ADT 等更新，没有新 A630 加速实现。 |
| WuuuWeiii/ios-sweet-ci | ea001518f154，10 月 25 日 15:51:52 | 新发现的同机型 Polaris GPU 实验代码，提交到 0147。描述自称从停用 fork 迁出的 CI 仓库；不能据此断言它是 luuu20309 的迁移仓库。 |
| joranv1/ios-sailfish | 6f27e41ff249，10 月 23 日 03:12 | 比旧交接固定的 ce637521 多 35 个提交，新增 Wi-Fi、触摸中断、软件/GPU 路径性能优化。硬件是 A530。 |
| NWylynko/ios-flame | 745c4341af86，10 月 22 日 23:58 | Pixel 4 / SM8150 / A640；有 GPU 提交、异步呈现、Turnip、Metal 转译实现。 |
| oosexgang/hackios-xnu-kernel | d82c64a3a31c，10 月 23 日 22:25 | 相对已核对的 8e611870 只改 setup.sh 和 UEFI loader；没有新的 GPU 差异。 |
| Luoxueowo/ios-polaris | 44556dec6995，10 月 21 日 18:44 | 仍为旧 init 提交，无新更新。 |

## 优先借鉴的实际代码

1. **Polaris 0136/0137：启动状态探测。** 读取 GCC GPU 时钟状态；后续提交承认 QFPROM 读取会挂总线，因此默认跳过。0137 同时保留了入口 `optin != 1` 判断，后面的 `optin >= 2` 分支实际不可达。这是需要审阅而非直接粘贴的证据。
2. **Polaris 0138/0139：CX 供电、GPUCC 时钟、GMU ITCM 装载与逐字读回。** 对 A630 的参考价值最高；需要改造成适配本地 0095、保持 FT8716 的独立补丁。0138 有先读取 GPUCC 再检查 GCC 可达性的顺序问题，部分必需时钟/RCG 返回值被忽略，失败后没有完整资源撤销。0139 输出“CM3 kept in reset”但没有在装载前显式设置 reset；不能把日志文案当作状态证明。
3. **Polaris 0140–0147：CM3 启动、HFI 队列、缓存/地址与 SPTPRAC 排查。** 上游最新代码仍写明 `GX/CP still untouched`。0147 的 SPTPRAC 超时只打印结果，随后继续 HFI；不能照搬这个失败处理。0141 依赖低位 HFI 内存与 `sweet_dbg_dump_now()`，后者写上游定义的 oops 分区，不能直接迁移到本机既有日志布局。
4. **Flame：GPU BO/IOVA、提交/完成同步、异步呈现与用户态 Turnip/Metal。** 0095 GPU present、0115 GPU v2/Turnip，以及 phone/metal2、phone/turnip 提供较完整接口参考。README 报告 A640 上 Metal 渲染桌面约 20–26 fps；这是该作者的实测声明，不是本机测量。A640 GMU 固件/内存映射/供电与 A630 不同，不能刷其镜像或只换芯片名。
5. **Sailfish 0180/0182：后续性能参考。** 保持 idle CPU cache、只刷写过的 BO 页面、将 GPU 呈现移出 backboardd swap 调用、触摸 move 合并。0182 依赖已经工作的 GPU/BO/同步接口；它不是 A630 初始化补丁。CPU boost 的电压和频率不适用于本机。

## 明确不直接合入

- Polaris 0125 scaler：`externalMethod` 不执行缩放而统一返回成功。服务匹配成功不等于工作完成，可能造成错误输出或隐藏失败；也没有证据证明它修复本机 PosterBoard 的重复初始化断言。
- Polaris 0143/0144 SMMU bypass/连续写不同 S2CR 测试：不是带隔离、所有权与撤销的映射实现。源码还明确记录 offset 0x6000 曾导致其手机复位，应作为失败教训保留。
- 全量上游内核/镜像：新 CI 仓库没有本地 FT0094/0095 的 FocalTech 补丁；还改变 machine config、启动诊断和分区日志路径。不能覆盖已确认的触摸基线。

## 验证边界与下一步

Actions **40174404768** 在提交 ea001518f154 上 `completed/success`。完整日志证明 0138、0139、0140、0147 被应用，power/gmu/cm3/smmu 四个源文件有编译命令；未见这些补丁被跳过。日志中跳过的是上游通用 TrustCache 等其他补丁。**CI 成功不证明手机 CM3/HFI 成功，更不证明桌面 GPU 加速。** 仓库树只有 workflow 和 patches，未找到原始手机运行日志/测试报告。

建议先借鉴并修正 Polaris 的 GCC/CX/GMU 实现，以保留 FT0095 的独立可关闭补丁取得供电和 ITCM 读回证据；其后验证 HFI、隔离内存映射、CP fence 和输出缓冲，最后接 Flame/Sailfish 的用户态接口。本次只完成源码核对，未宣称 GPU 已工作。

## 可复查来源

- 旧源仓库：https://github.com/luuu20309/ios16_on_polaris_mix2s/commit/c9508ae86deb7059a923b3b1a494859aae24c34e
- 新 Polaris 固定代码：https://github.com/WuuuWeiii/ios-sweet-ci/tree/ea001518f1540ee1bce87a7f405d0f301eb3b9f3
- 新 Polaris CI：https://github.com/WuuuWeiii/ios-sweet-ci/actions/runs/40174404768
- Flame：https://github.com/NWylynko/ios-flame/tree/745c4341af86089efb0df851fa8a27fb8face624
- Sailfish：https://github.com/joranv1/ios-sailfish/commit/6f27e41ff249
- HackiOS 差异：https://github.com/oosexgang/hackios-xnu-kernel/compare/8e61187098f2c26c5daee8f9f72b6416aa64b9ab...d82c64a3a31cd27a0a0afbf6e213e467e44b1b4c

源码副本与完整 CI 日志只保存在忽略目录 `out/upstream-audit-20261025/`。新 Polaris 仓库未包含许可文件；直接复用代码前还需确认其授权与来源，当前可先使用公开实现作流程参考。
