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

只读入口为 `polarisgmutest=1/2`：分别为 GCC AON 和只读 CX 状态。
后续加入独立的 mode 4 电源门实验，见下节。
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

实现只允许取得初始 PWR_ON=0、SW_COLLAPSE=1、HW_CONTROL=0、SW_OVERRIDE=0 的 CX GDSC。
本机最终 DT 的 `qcom,clk-dis-wait-val=8` 对应 GDSCR bits 15:12；XNU 实测继承值为 2。
按 [厂商 probe](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/blob/c7cb50c18f812629b9620b2b8fa684bc40d82080/drivers/clk/qcom/gdsc-regulator.c#L675)
先在关闭态设置并读回 8；设置被拒绝时不尝试上电。清除 collapse 位后按本机 DT 等待最多 500us，从 GPUCC 的 `0x1540` 状态别名确认上电。
仅在两个电源状态一致、GMU 时钟仍未请求且停止时，恢复原 collapse 请求。
按厂商 `gdsc_disable` 先留出 1us memory power-down 等待；写 collapse 后等待 1us，
再按 DT `qcom,no-status-check-on-disable` 等待 100us，并记录两个电源状态。
只有二者均确认关闭才恢复原等待值，随后重新检查等待值、电源状态和控制位。
独立 TZ/HYP 投票若仍保持上电，会单独报告“请求已恢复、域仍开启”，不会强制关闭。
硬件接管、状态不可读或 GMU 时钟活动时停止并保留当前供电，不能声称完成回退。

没有共享电压、GMU/PLL/时钟、GX、reset、SMMU 或固件写入。
主机编译实际 C helper，28 个供电/时序故障场景通过；既有 14 个事务场景、六个 CX 读门槛场景
和内存边界检查也通过。成功结果只证明局部 CX 开关及回退，尚不证明带时钟负载时的供电、
GMU 执行或图形加速。内核 CI 和临时启动结果持续记录在 `NOTES.md`。


## 2026-10-25 mode 4 真机结果

CI 40191126435 / `65a5ced` 的临时镜像在本机成功完成局部 CX 周期：
`0x00222001` → 等待值 8 的 `0x00228001` → 上电 `0xf8228000`，
HW 状态同时为 `0x80001fe0`；随后关闭并恢复到原值 `0x00222001`，
最终 HW 状态 `0x00005ff4`。GMU 时钟全程未启用。用户确认锁屏和触摸正常；
新日志明确为 mode 4、result=0。该结果仅证明当前继承供电条件下的局部电源门和时序恢复。
下一阶段是按厂商 RCG/branch 语义验证 XO 时钟及 CM3 复位状态，再恢复时钟与 CX。


## 后续 mode 5：XO 时钟与有条件的 CM3 状态读取

mode 5 在已验证的 CX 周期内部执行，默认不启用。依据固定 MiCode 提交 `c7cb50c18f81`：

- `gpucc-sdm845.c:220-245` 的 v2 频率表支持 XO 19.2 MHz，parent selector=0，HID=1。
  `:414-429` / `:458-469` 给出 CX GMU / CXO 的 CBCR `0x1098` / `0x109c`。
- 同文件 `:820-824` 把 GMU sleep/wake 字段 bits 11:4 都设置为 15；探针保存旧值，
  只在分支关闭时设置，回退时在确认分支关闭后恢复。
- [clk-rcg2.c](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/blob/c7cb50c18f812629b9620b2b8fa684bc40d82080/drivers/clk/qcom/clk-rcg2.c#L492)
  的 safe-config 先 force-enable root，再配置/等待 UPDATE，最后撤销 force。
  探针只接受空闲、未强制开启、XO parent、无硬件控制的原配置；不取得 PLL 或新电压投票。
  ROOT_OFF、UPDATE 和分支状态按厂商 500 次 / 1us 轮询；超时为失败。
- 恢复顺序是 GMU 分支停止、RCG 原配置和强制位恢复、撤销自己新增的 CXO enable、
  GMU sleep/wake 恢复，最后才允许 CX collapse。继承的 CXO request 保留。
  活动/未知资源、无法确认的 CM3 reset、时钟恢复失败均保留 CX，并明确报告失败。
- 本机 GMU DT 包含 AXI/MEMNOC；同机 Polaris 0138 还打开 SNOC。
  不能从 core clock ready 推断 GMU 总线可达。本次不写 GCC；只有这三项状态均显示运行，
  才读取一次 CM3 SYSRESET `GMU+0x14000`。否则记录总线未确认并恢复时钟。
  不写 GMU、复位、固件、GX、SMMU 或 PAS；mode 3 仍不可由启动参数启用。

24 个实际 C helper + CX 外层事务场景通过，覆盖各阶段故障、三个独立总线门槛、
未复位/不可读 CM3、继承 CXO、无关字段变化和回退失败。该实现尚需 CI 和真机验证；
mode 4 的成功不替代带时钟负载的验证。


## 2026-10-25 mode 5 真机结果

CI 40192935142 / `d3151f4` 的新日志确认 GMU CBCR 从 `0x80004220` 经等待字段
`0x80004ff0` 到运行态 `0x00004ff1`，XO RCG 从 CMD/CFG=`0x80000000/0`
到 `0/1`。CXO 继承 request=1、运行态为 1，没有撤销其请求。
GMU 总线门槛未满足，按设计没有读取 CM3。回退后 RCG/GMU 等待和请求全部恢复，
CX 再关闭且 GDSCR 回到 `0x00222001`。用户确认锁屏和触摸正常。
总线三个值只在 CX 上电前分别记录过，不能据此推断上电后的各自状态。
下一实验先逐项记录和管理本地总线时钟请求，再有条件读取复位状态。


## mode 6 总线请求依据与边界

重新读取 recovery 的 `/sys/firmware/fdt`，与既有 492460 字节导出逐字节相同。
GCC provider 是 `qcom,gcc-sdm845-v2.1`、`reg=0x00100000+0x001f0000`，
本机 GMU 的 AXI/MEMNOC specifier 分别是 **26 / 41**。
早期 MiCode 头文件为 20 / 35，不能套用其编号。
[LineageOS 固定提交 aa8adfe9](https://github.com/LineageOS/android_kernel_xiaomi_sdm845/tree/aa8adfe9bf212c6e93238a86980b4024da4f819a)
的 `include/dt-bindings/clock/qcom,gcc-sdm845.h:46,61-62` 正好对应 AXI=26、
MEMNOC=41、SNOC=42；驱动同时匹配 v2.1。它不是当前 recovery 二进制的构建来源证明，
但与最终 DT 兼容项/编号匹配，并独立确认早期 MiCode 的寄存器语义：

| 时钟 | GCC 偏移 | 本地请求 | 停止语义 |
| --- | --- | --- | --- |
| DDR AXI | 0x44038 | bit 0 | BRANCH_VOTED |
| GPU MEMNOC | 0x7100c | bit 0 | BRANCH_VOTED |
| GPU SNOC DVM | 0x71018 | bit 0 | BRANCH_HALT |

来源为上述提交的 `gcc-sdm845.c:1786,1993,2006`、绑定数组与 v2.1 匹配表；
`clk-branch.c:80-105` 确认启用等待 500us，voted disable 等待 10us。
mode 6 在已验证的 CX/XO 周期中记录三项初值，按 GMU DT 顺序先 AXI、MEMNOC，
再检查同机 Polaris 0138 使用的 SNOC。只取得初始 bit0=0 的本地请求；
初始 bit0=1 的请求保持原样，若仍停钟则超时退出。只有三项再次确认运行才读取一次 SYSRESET。
回退按反序撤销本次取得的请求，voted clock 不要求所有外部投票也关闭；
本地请求读回失败或 SNOC 无法停钟则保留 CX/时钟并报告。
CM3 不为 1 或不可读时保留依赖，不写复位或固件。

41 个实际 helper/事务场景通过（其中新增 17 个总线场景）。尚未真机验证 mode 6；
它不证明总线带宽、RPMh 电压投票、SMMU 隔离、固件运行或 GPU 加速。


## mode 6 首次真机结果与触摸限制

CI 40194788540 / `b29375a` 的新日志记录 CX/XO 开启后 AXI、MEMNOC、SNOC 初值分别为
`0x80000000 / 0x80004220 / 1`。只添加 AXI/MEMNOC 本地请求后，三项均就绪，
读到 **CM3 SYSRESET=1**；随后撤销本次请求并恢复全部时钟/CX 状态。
SNOC 原有请求保留。这证明局部总线请求与复位寄存器访问，不代表固件已经执行。

用户晚些时候发现触摸不响应，日志同时明确记录 FT8716 线程已停止。
现有 0094/0095 触摸实现仍有 12000 次、每轮 20ms 的实验上限；未出现连续坏帧的停止提示。
因此需要区分线程正常耗尽与息屏相关故障。用户要求重启同镜像做即时触摸对照；
已临时重复启动，结果待观察，尚不能声称 mode 6 用户体验通过。

## mode 6 同镜像复测

用户确认刚到锁屏和自动息屏后触摸均正常。新 ring（229543 字节、flush 17）
再次确认 SYSRESET=1、AXI/MEMNOC 请求撤销、SNOC 原请求保持，以及完整时钟/CX 恢复。
捕获 48 条触点 down/up，记录 display off/on，未出现 FT stopped 或连续坏帧停止。
首次晚发触摸失效符合现有 12000 次轮询耗尽路径，不能归因于自动息屏。

0097 移除该实验期限，保留 20ms 等待和连续八次失败后的释放/停止。
越过旧上限后仅打印一次标记，以便下次真机日志确认。
[同平台 ft5x46 固定源码](https://github.com/LineageOS/android_kernel_xiaomi_sdm845/blob/aa8adfe9bf212c6e93238a86980b4024da4f819a/drivers/input/touchscreen/ft5x46/ft5x46_ts.c#L4201)
注册持续 IRQ 服务，另有明确 suspend/resume/remove 生命周期；没有按采样次数结束服务。
这里只借鉴生命周期，不搬用 Linux IRQ 或休眠机制，硬件参数仍沿用本机已验证的 0095。
四个实际 C 轮询/解析/HID 回归场景通过，长时间手机验证待完成。

0097 的 RELEASE CI 40197718687（`5972647`）已成功，镜像核对后临时启动。
用户正在验证刚启动及至少十分钟后的触摸，待回 recovery 核对越过 12000 次标记和后续触点。
此阶段 GPU 仍是已验证的 mode 6，没有启动 TCM/固件实验。

FT0097 长测已完成：用户确认十分钟正常；新 ring 出现越过 12000 次的标记，
之后仍有 27 条触点事件（全程 65 条），无 FT stopped/连续坏帧停止。
mode 6 再次完整恢复，SYSRESET=1、CX 最终 0x00222001。
CI 40197718687 镜像成为后续 GPU 实验的持续触摸基线；既有用户态崩溃仍需另行解决。
