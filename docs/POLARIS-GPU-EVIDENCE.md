# Polaris A630：设备树与参考实现依据（2026-10-25）

当前目标是小米 MIX 2S（`polaris`，SDM845 / Adreno 630v2）。`ios-sweet`
原始目标 SM7150 / A618 的地址和供电参数不能作为本机默认值。
首次核对阶段只读取已有本机导出、公开源码和 CI，修正离线解析工具。
随后用户授权临时测试，已启动下述只读 CX 探针；逐次结果见 `NOTES.md`。

## 依据与适用范围

- 本机 Recovery 导出的最终 FDT：忽略目录 `out/gpu-20261025/live.dtb`。
  同目录 `cmd-db.txt` 是已取得的内核 debugfs 文本，`clocks.txt` /
  `regulators.txt` 是 Recovery 时刻的状态。它们不是当前 XNU 状态。
  不提交这些导出、完整设备树、固件或设备标识。
- 小米 [polaris-o-oss](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/c7cb50c18f812629b9620b2b8fa684bc40d82080)：
  `sdm845-gpu.dtsi`、v2 覆盖、`gpucc-sdm845.c`、`kgsl_gmu.c` 和
  `msm_bus_fabric_rpmh.c` 提供厂商绑定及寄存器语义。KGSL 内核源码可以参考，
  Android 闭源 GLES 用户态不能直接当成 Darwin 驱动。
- Linux 固定提交 [21a7f44fd076](https://github.com/torvalds/linux/tree/21a7f44fd0765fcc5194130b28767786eba1d1e7)：
  [sdm845.dtsi](https://github.com/torvalds/linux/blob/21a7f44fd0765fcc5194130b28767786eba1d1e7/arch/arm64/boot/dts/qcom/sdm845.dtsi#L4940)、
  [a6xx_gmu.c](https://github.com/torvalds/linux/blob/21a7f44fd0765fcc5194130b28767786eba1d1e7/drivers/gpu/drm/msm/adreno/a6xx_gmu.c)、
  `a6xx_gpu.c`、`a6xx_hfi.c` 用来核对 A630 legacy 路径。
  主线和下游的时钟 ID、IOMMU 绑定及版本命名不能混用。

## 已解析到实际节点的本机关系

下表是驱动需要的硬件事实摘要，不是可直接写入的寄存器序列。
电压级别也不能按 DTS 属性名中的 `microvolt` 擅自解释成物理微伏。

| 项目 | 本机最终 DT 及 phandle 解析结果 | 对实现的约束 |
| --- | --- | --- |
| GPU 版本 | `qcom,chipid=0x06030001`；GPUCC/GFXCC 都是 `sdm845-v2` | 使用 v2 档位；DT 声明不是 XNU 读到的芯片 ID |
| GPU / GMU | GPU `0x05000000+0x40000`；GMU `0x0506a000+0x30000`；PDC `0x0b200000+0x300000` | 先证明电源和总线可达，再读寄存器 |
| 时钟控制器 | GPUCC 与 GFXCC 都声明 `0x05090000+0x9000` | 两套驱动描述共享窗口，不能当成独立资源随意复位 |
| CX / GX | GDSCR 分别 `0x0509106c` / `0x0509100c`；CX `hw-ctrl-addr` phandle 指向 `0x05091540` | `hw-ctrl-addr` 的单元是引用，不能把 phandle 数值当 MMIO 地址 |
| 供电关系 | GPUCC 的 CX/MX 分别指向 `pm8998_s9_level` / `pm8998_s6_level`；GFXCC 指向 `pm8005_s1_level`；CX 又依赖 MX | 对照 ARC regulator 与父供电语义，不能只投 `gfx.lvl` 就访问 RBBM |
| Apps RSC | mailbox `0x179e0000+0x100`、TCS `0x179e0d00+0x3000`，DRV ID 2 | 不是 Sweet 的 RSC 地址；DT 不证明 XNU 已取得某个 TCS 的使用权 |
| GMU 时钟 | GPUCC provider 的 `gmu_clk` / `cxo_clk`，GCC provider 的 `axi_clk` / `memnoc_clk` | 时钟 specifier 按各 provider 的 `#clock-cells` 解析；数值 ID 不是寄存器偏移 |
| GMU 频率 | DT 为 0 / 200 / 500 MHz；下游 v2 时钟表另支持 XO 19.2 MHz | 19.2 MHz TCM 访问实验不等于已经验证可启动 GMU 固件 |
| GPU/总线档位 | 初始 pwrlevel 6；7 个工作档 257–710 MHz 加关闭档；13 个 DDR case、2 个 CNOC case | 本次逐项与 `polaris_bw_levels.py` 的频率、case、上下界、路径核对通过 |
| GMU IOMMU | user/kernel 分别指向 `arm,smmu-kgsl@5040000`，参数 4/5；SMMU 依赖 CX 与 MEMNOC 时钟 | IOMMU 参数不能直接当 context-bank 编号或照搬别的板子的 S2CR |
| 受保护区域 | 另一个 `qcom,kgsl-iommu` 节点含 `qcom,protect=<0x40000 0xc000>` 和 `qcom,hyp_secure_alloc` | 必须同时看厂商 KGSL 与通用 SMMU 节点，不能漏掉保护约束 |
| Zap | PAS 13，`a630_zap`，memory-region 指向 `0x8c415000+0x2000`、`no-map` | 固件和保留区是本机条件，不能照搬 A642L/A640 加载器 |
| command-db | 节点名 `cmd-db@861e0000`，实际 `reg=<0x0c3f000c 0x8>` | 这是 locator 字段；不能拿节点名或早期转储地址当本次 DB 地址，更不能由此直接映射 XNU 物理内存 |

## 从其他项目借鉴什么

- [HackiOS 内核](https://github.com/oosexgang/hackios-xnu-kernel/tree/d82c64a3a31cd27a0a0afbf6e213e467e44b1b4c)
  与 [Topaz e4772911](https://github.com/MakrSas/ios-topaz/tree/e4772911e116ab4ab6c4ac1f38e6106fefe8b483)
  提供 Mesa freedreno、EAGL、IOKit user-client、IOSurface 的集成参考。
  Topaz 的 `userland/gpu/eagl.c` 和 Mesa 目录已确认存在；README 声称 UI 已由 GPU 绘制，
  同时在待办中注明当前还会由 CPU 复制 GPU 输出。不能把此声明扩大成已完成零拷贝呈现。
- [Flame 745c4341](https://github.com/NWylynko/ios-flame/tree/745c4341af86089efb0df851fa8a27fb8face624)
  包含 `phone/metal2`、Turnip 及呈现接口。README 报告 A640 的 UI/应用 Metal 渲染，
  是后续用户态参考，不是本机 A630 启动证据。
- 同机型 WuuuWeiii 的 0138–0148 实验提供 CX、TCM、CM3/HFI 排查线索，
  但其 SMMU 试写、忽略失败和资源撤销问题不能复制；具体已核对提交见
  [上游审阅](GPU-UPSTREAM-AUDIT-20261025.md)。本次未重新断言它的最新 HEAD 或真机成功状态。

## 主线指出的两个关键差异

1. [A630 设置 legacy/HFI v1](https://github.com/torvalds/linux/blob/21a7f44fd0765fcc5194130b28767786eba1d1e7/drivers/gpu/drm/msm/adreno/a6xx_gmu.c#L2412)。
   [legacy 固件加载](https://github.com/torvalds/linux/blob/21a7f44fd0765fcc5194130b28767786eba1d1e7/drivers/gpu/drm/msm/adreno/a6xx_gmu.c#L894)
   把至多 32 KiB 平铺数据写到 ITCM；后续架构走分块加载。因此“只换地址、SID、固件名”不成立。
2. [a6xx_gmu_resume](https://github.com/torvalds/linux/blob/21a7f44fd0765fcc5194130b28767786eba1d1e7/drivers/gpu/drm/msm/adreno/a6xx_gmu.c#L1322)
   先取得资源、开时钟、设置启动带宽，再启动固件/HFI。此前的
   [GX domain get](https://github.com/torvalds/linux/blob/21a7f44fd0765fcc5194130b28767786eba1d1e7/drivers/gpu/drm/msm/adreno/a6xx_gmu.c#L1271)
   在 A6xx 只是管理引用计数，注释明确不直接上电。不能简化为“先强制开 GX，再启动 GMU”。

## 本次落实的解析修正和验证

- ARC 按主线 `a6xx_gmu_rpmh_read_arc` 只去掉末尾零填充；保留起始 off=0
  和重复 VLVL 的原始位置。表内值是虚拟电压级别，位置才是 vote index。
  仍拒绝长度异常、全零表、下降序列、中间归零后又上升等异常。
  旧版 MiCode `rpmh_arc_cmds` 遇重复会截断，和当前主线处理不同；解析器保留完整索引，
  不声称复制了旧厂商消费者的行为。
- BCM 保留 width=0 的合法元数据。主线有独立 fixed-vote 路径，但不能仅靠 width=0
  推导 BCM 类型或生成投票。仍拒绝长度、unit=0 和越界 VCD。
- 新增 aux 解码接口直接读取已有导出的辅助数据，不伪造文本里没有的
  binary header、priority 或 version。完整二进制解析器复用同一接口。
- 18 项相关主机测试通过。已有本机文本中 3 个 ARC 资源的 aux 成功解码；
  38 个 BCM 中 7 个 width=0 项也成功保留。真实字节留在忽略目录，提交的测试仅含合成数据。

## 当前验收点

`059bc5b` 的 RELEASE CI [40185293459](https://github.com/chengdongjun6-dot/ios16_on_polaris_mix2s/actions/runs/40185293459)
已成功；完整日志确认 0096 应用、`polaris_gmu_tcm_test.c` 编译和内核产出。
这是已存在的 CI，无需重建。随后已下载并核对内核 SHA-256
`4d1190db841d9eaf20a6a2bb3923d397b0285a68c733af374d83f6496bedf6e0`，
保留 FT0095 shim/DTB/触摸配置打包 mode 2，经用户授权临时启动。
用户已确认锁屏和触摸正常，并返回 recovery。本次新日志读到
`CX GDSCR=0x00222001`：bit31 PWR_ON 为 0，bit0 SW_COLLAPSE 为 1，说明探测时 CX 关闭。
GMU/CXO 时钟为 `0x80004220/0x80000001`，RCG 为 `0x80000000`，均未运行。
这是 XNU 读回结果；日志里仍有一次 PosterBoard sig5，不能声称全部用户态稳定。

现有入口只接受 `polarisgmutest=1/2`：分别为 GCC AON 和只读 CX 状态。
mode 3 的 TCM 核心虽存在，启动入口仍将其挡住；不能按旧注释宣称 mode 2 会写 TCM。

这次日志暴露了旧 mode-2 分支将时钟的 bit31=OFF 语义误用于 GDSCR，
因此在 CX 关闭时仍读取了五项 GPUCC 状态，没有按预期停止；没有 GMU 访问或 GPU 写操作。
[MiCode gdsc_is_enabled](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/blob/c7cb50c18f812629b9620b2b8fa684bc40d82080/drivers/clk/qcom/gdsc-regulator.c#L168)
明确要求 GDSCR bit31=ON 且 SW_COLLAPSE=0。
提交 `8bdf56d` 已分离两种状态位名称，并让实际只读分支复用经过主机测试的访问门槛；
14 个事务场景、六个 CX 门槛场景和内存边界检查通过。该修正尚未经过新的 macOS CI 或手机启动，
旧测试镜像不应重复使用。

下一步设计有来源、可撤销的 CX/MX/RPMh 与 GDSC 上电操作，并在下一次内核构建中包含上述修正。
GMU/HFI、PAS13、隔离映射、CP 写回/fence 和 Mesa 呈现仍未在本机 XNU 上验证。

## 后续最小实验：局部 CX GDSC 开关

`polarisgmutest=4` 默认关闭，用于检验继承的共享供电条件能否支持局部 CX 电源门。
它不发送 CX/MX ARC 投票。厂商 `rpmh_regulator_arc_get_voltage_sel` 返回驱动缓存，
不能把 Recovery 的读数当作 XNU 接手时可以恢复的原始投票。

实现只允许取得初始 PWR_ON=0、SW_COLLAPSE=1、HW_CONTROL=0 的 CX GDSC；
清除 collapse 位后按本机 DT 等待最多 500us，从 GPUCC 的 `0x1540` 状态别名确认上电。
仅在两个电源状态一致、GMU 时钟仍未请求且停止时，恢复原 collapse 请求。
按照 DT `qcom,no-status-check-on-disable` 和厂商 `gdsc_disable` 等待 100us，再记录最终状态。
独立 TZ/HYP 投票若仍保持上电，会单独报告“请求已恢复、域仍开启”，不会强制关闭。
硬件接管、状态不可读或 GMU 时钟活动时停止并保留当前供电，不能声称完成回退。

没有共享电压、GMU/PLL/时钟、GX、reset、SMMU 或固件写入。
主机编译实际 C helper，19 个供电故障场景通过；既有 14 个事务场景、六个 CX 读门槛场景
和内存边界检查也通过。成功结果只证明局部 CX 开关及回退，尚不证明带时钟负载时的供电、
GMU 执行或图形加速。内核 CI 和临时启动结果持续记录在 `NOTES.md`。
