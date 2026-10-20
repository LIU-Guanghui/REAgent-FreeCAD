# REAgent-FreeCAD

[English](../README.md) | 简体中文

**让 Agent 把 STEP 零件重建为可编辑的 FreeCAD 模型。**

REAgent-FreeCAD 面向单实体 STEP/STP 零件，将其重建为 FreeCAD 原生特征历史。Agent 在只读的 Source 窗口中观察参考几何，在独立的 Target 窗口中建模，并在过程中比较结果。项目包含本地 MCP 服务、FreeCAD 的 REAgent 工作台和重建 Skill。

## 功能介绍

- **观察参考模型：** 分析几何、尺寸和视图，再选择建模方式。
- **构建可编辑特征：** 创建带参数的原生 PartDesign 特征。
- **比较并改进结果：** 结合几何比较和视觉证据，决定下一步建模操作。

## 安装与配置

需要 FreeCAD 1.1、Python 3.10 或更新版本、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及支持本地 stdio MCP 服务和图像的 Agent 客户端。以下以 Windows 和 Codex 为例，PowerShell 命令均在仓库根目录执行。`~` 表示当前用户目录，例如 `C:\Users\your-name`。

### 1. 安装 MCP 服务

下载或克隆本仓库，在仓库根目录执行：

```powershell
uv sync --locked
```

这会将项目和锁定版本的依赖安装到 `.venv`。请保留仓库位置，后面的客户端配置会使用其中的启动器。

### 2. 安装 FreeCAD 工作台

关闭 FreeCAD，将仓库中的 `addon/REAgent/` 文件夹复制到 FreeCAD 安装目录下的 `Mod` 文件夹。以下以 `C:\Program Files\FreeCAD 1.1` 为例，请按实际安装路径调整；写入 `Program Files` 时可能需要管理员权限：

```powershell
Copy-Item -LiteralPath '.\addon\REAgent' -Destination 'C:\Program Files\FreeCAD 1.1\Mod' -Recurse -Force
```

安装后应存在 `<FreeCAD 安装目录>/Mod/REAgent/InitGui.py`。重新打开 FreeCAD，即可在工作台选择器中找到 **REAgent**。

### 3. 连接 Agent 客户端

使用 Codex 时，在 `~/.codex/config.toml` 中加入以下配置，将 `command` 中的示例路径替换为本机仓库的绝对路径：

```toml
[mcp_servers.REAgentFreeCAD]
command = "C:/path/to/REAgentFreeCAD/.venv/Scripts/REAgentFreeCAD.exe"
```

重新加载客户端并确认服务可用。

### 4. 配置重建 Skill

使用 Codex 时，将整个 `.agents/skills/reagentfreecad/` 文件夹（包括 `references/` 和 `agents/`）复制到 `~/.agents/skills/reagentfreecad/`：

```powershell
New-Item -ItemType Directory -Path '~/.agents/skills' -Force | Out-Null
Copy-Item -LiteralPath '.\.agents\skills\reagentfreecad' -Destination '~/.agents/skills' -Recurse -Force
```

也可以直接以本仓库作为 Codex 工作目录，使用仓库自带的[重建 Skill](../.agents/skills/reagentfreecad/SKILL.md)。

## 快速开始

1. 分别打开两个 FreeCAD 实例，在两个窗口中都选择 **REAgent** 工作台。
2. 在一个窗口点击 **Source**，在另一个窗口点击 **Target**，保持两个窗口可见。状态标签会显示各自角色；完成后可用 **Stop** 解除绑定。
3. 在 Agent 客户端中提供 STEP 文件路径和结果保存位置。例如：

   ```text
   使用 $reagentfreecad 重建 C:/parts/source.step 结果保存到当前目录。
   ```

4. 在 Target 中查看建模过程。Agent 完成后，检查特征树和比较结果，再用 FreeCAD 打开保存的 `.FCStd` 文件继续编辑。

## 接口列表

当前提供 55 个公开 MCP 接口，名称与用途如下。参数以 `agent_get_capabilities` 返回为准，接口定义见 [tools_schema.py](../src/REAgentFreeCAD/tools_schema.py)。

### 重建管理

| 接口 | 用途 |
| --- | --- |
| `agent_ping` | 检查 Source 和 Target 的连接状态 |
| `agent_get_capabilities` | 查询接口目录、参数结构与运行环境能力 |
| `agent_get_guidance` | 读取重建知识卡及其目录 |
| `agent_start_session` | 从 STEP 创建会话，或恢复已有 FCStd 重建会话 |
| `agent_get_state` | 读取会话状态、模型对象及异步操作的进度和结果 |
| `agent_observe` | 获取几何、拓扑、尺寸、截面与视图证据 |
| `agent_validate` | 检查模型结构，并按需比较几何或审计参数 |
| `agent_undo` | 撤销当前原生历史栈顶、属于本会话的 REAgent 事务 |
| `agent_redo` | 重做当前原生历史栈顶、属于本会话的 REAgent 事务 |
| `agent_save` | 将 Target 模型保存为会话分配路径下的 FCStd 文件 |
| `agent_close_session` | 显式丢弃未保存修改并关闭本会话文档 |
| `agent_journal` | 读取或写回会话日志 |
| `agent_read_freecad_log` | 读取选定 FreeCAD 实例的原生日志 |

### 原生建模

| 接口 | 用途 |
| --- | --- |
| `PartDesign_Body` | 创建 Body |
| `PartDesign_NewSketch`、`Sketcher_MapSketch`、`Sketcher_EditSketch`、`Sketcher_ValidateSketch` | 创建、附着、编辑与检查草图 |
| `Part_DatumPlane`、`Part_DatumLine`、`Part_DatumPoint`、`Part_CoordinateSystem` | 创建基准平面、轴线、点与坐标系 |
| `PartDesign_AdditiveBox`、`PartDesign_SubtractiveBox`<br>`PartDesign_AdditiveCylinder`、`PartDesign_SubtractiveCylinder`<br>`PartDesign_AdditiveCone`、`PartDesign_SubtractiveCone`<br>`PartDesign_AdditiveSphere`、`PartDesign_SubtractiveSphere`<br>`PartDesign_AdditiveEllipsoid`、`PartDesign_SubtractiveEllipsoid`<br>`PartDesign_AdditiveTorus`、`PartDesign_SubtractiveTorus`<br>`PartDesign_AdditivePrism`、`PartDesign_SubtractivePrism`<br>`PartDesign_AdditiveWedge`、`PartDesign_SubtractiveWedge` | 创建加料或减料基本几何：长方体、圆柱体、圆锥体、球体、椭球体、圆环体、棱柱体与楔体 |
| `PartDesign_Pad`、`PartDesign_Pocket` | 拉伸与凹槽 |
| `PartDesign_Revolution`、`PartDesign_Groove` | 旋转与旋转切除 |
| `PartDesign_AdditiveLoft`、`PartDesign_SubtractiveLoft` | 加料和减料放样 |
| `PartDesign_AdditivePipe`、`PartDesign_SubtractivePipe` | 加料和减料扫掠 |
| `PartDesign_Hole` | 孔 |
| `PartDesign_Boolean` | 布尔 |
| `PartDesign_Fillet` | 圆角 |
| `PartDesign_Chamfer` | 倒角 |
| `PartDesign_Draft` | 拔模 |
| `PartDesign_Thickness` | 厚度 |
| `PartDesign_Mirrored` | 镜像 |
| `PartDesign_LinearPattern`、`PartDesign_PolarPattern` | 线性与环形阵列 |

## 项目结构

以下列出仓库中的全部项目文件，并标注各文件的主要职责。

```text
REAgentFreeCAD/
├── .agents/
│   └── skills/
│       └── reagentfreecad/                             # 重建 Skill
│           ├── agents/                                 # 客户端元数据
│           │   └── openai.yaml                         # Skill 展示与客户端元数据
│           ├── references/                             # 按需加载的参考指南
│           │   ├── features/                           # 特征建模指南
│           │   │   ├── body.md                         # Body 指南
│           │   │   ├── boolean.md                      # 布尔指南
│           │   │   ├── chamfer.md                      # 倒角指南
│           │   │   ├── datum.md                        # 基准几何指南
│           │   │   ├── draft.md                        # 拔模指南
│           │   │   ├── entry.md                        # 特征指南入口
│           │   │   ├── extrude.md                      # 拉伸与凹槽指南
│           │   │   ├── fillet.md                       # 圆角指南
│           │   │   ├── hole.md                         # 孔指南
│           │   │   ├── loft.md                         # 放样指南
│           │   │   ├── mirror.md                       # 镜像指南
│           │   │   ├── pattern.md                      # 阵列指南
│           │   │   ├── pipe.md                         # 扫掠指南
│           │   │   ├── primitives.md                   # 基本几何指南
│           │   │   ├── revolve.md                      # 旋转与旋转切除指南
│           │   │   ├── sketch.md                       # 草图指南
│           │   │   └── thickness.md                    # 厚度指南
│           │   ├── foundations/                        # FreeCAD基础
│           │   │   ├── brep-topology.md                # B-Rep 几何与拓扑
│           │   │   ├── entry.md                        # 基础知识入口
│           │   │   ├── freecad-document.md             # FreeCAD 文档与对象模型
│           │   │   ├── geometry-parameterization.md    # 几何参数化原则
│           │   │   ├── persistent-references.md        # 持久引用与拓扑变化
│           │   │   ├── tolerance-validity.md           # 容差与几何有效性
│           │   │   └── transactions-errors.md          # 事务、错误与恢复原则
│           │   └── workflow/                           # 重建流程指南
│           │       ├── entry.md                        # 重建流程入口
│           │       ├── observation.md                  # 源模型与目标模型观察
│           │       ├── session-and-isolation.md        # 会话隔离与恢复
│           │       └── validation.md                   # 结果核验与保存
│           └── SKILL.md                                # 重建 Skill 入口与运行规则
├── addon/
│   └── REAgent/                                        # FreeCAD 工作台与本地桥接
│       ├── analysis/                                   # 只读几何分析与证据
│       │   ├── __init__.py                             # 包初始化
│       │   ├── evidence.py                             # B-Rep 证据、关系与几何选择依据
│       │   ├── math3d.py                               # 数值、向量与拓扑名称工具
│       │   ├── measurements.py                         # 几何边界与尺寸测量
│       │   ├── predecessor.py                          # 修饰前几何边界的只读推断
│       │   ├── references.py                           # 几何引用结构与解析
│       │   ├── sampling.py                             # 裁剪面内的点与法向采样
│       │   ├── selection.py                            # 几何查询与当前子元素解析
│       │   └── topology.py                             # 拓扑详情、指纹与关系
│       ├── features/                                   # 原生特征实现
│       │   ├── common/                                 # 共用构建与引用逻辑
│       │   │   ├── __init__.py                         # 包初始化
│       │   │   ├── construction.py                     # Body 构建、预检与结果读回
│       │   │   ├── references.py                       # 原生引用与链接读回
│       │   │   └── transforms.py                       # 镜像与阵列的操作数和引用
│       │   ├── __init__.py                             # 包初始化
│       │   ├── body.py                                 # Body 原生实现
│       │   ├── boolean.py                              # 布尔原生实现
│       │   ├── chamfer.py                              # 倒角原生实现
│       │   ├── datum.py                                # 基准几何原生实现
│       │   ├── draft.py                                # 拔模原生实现
│       │   ├── extrude.py                              # 拉伸与凹槽原生实现
│       │   ├── fillet.py                               # 圆角原生实现
│       │   ├── hole.py                                 # 孔原生实现
│       │   ├── loft.py                                 # 放样原生实现
│       │   ├── mirror.py                               # 镜像原生实现
│       │   ├── pattern.py                              # 阵列原生实现
│       │   ├── pipe.py                                 # 扫掠原生实现
│       │   ├── primitives.py                           # 基本几何原生实现
│       │   ├── revolve.py                              # 旋转与旋转切除原生实现
│       │   ├── sketch.py                               # 草图原生实现
│       │   └── thickness.py                            # 厚度原生实现
│       ├── protocol/                                   # 桥接协议与共享契约
│       │   ├── __init__.py                             # 包初始化
│       │   ├── capabilities.py                         # 桥接能力发现
│       │   ├── contract.py                             # 共享操作、角色与参数契约
│       │   ├── logs.py                                 # 绑定实例的原生日志读取
│       │   ├── responses.py                            # 成功与失败响应元数据
│       │   └── server.py                               # JSON Lines 通信与 GUI 线程分派
│       ├── validation/                                 # 模型结构与几何核验
│       │   ├── __init__.py                             # 包初始化
│       │   ├── comparison.py                           # 尺寸、表面采样与定向布尔比较
│       │   ├── conclusions.py                          # 结构与参数探针事实整理
│       │   ├── handlers.py                             # 目标读回与按需诊断入口
│       │   ├── parameters.py                           # 参数扰动与恢复检查
│       │   ├── structure.py                            # 原生结构与单实体就绪检查
│       │   └── tolerance.py                            # 几何引用与诊断容差
│       ├── view/                                       # 视图、相机与显示
│       │   ├── __init__.py                             # 包初始化
│       │   ├── camera.py                               # 相机姿态、读回与恢复
│       │   ├── presentation.py                         # 对象显示与可见窗口状态
│       │   ├── queries.py                              # Source 与 Target 视图规划
│       │   └── rendering.py                            # 视图渲染、图像产物与拼图
│       ├── workspace/                                  # 文档、会话与恢复
│       │   ├── __init__.py                             # 包初始化
│       │   ├── executor.py                             # 原生事务、恢复与结果状态
│       │   ├── model.py                                # 工作区归属与结果选择
│       │   ├── persistence.py                          # FCStd 保存与路径保护
│       │   ├── properties.py                           # 属性元数据、编解码与草图读回
│       │   ├── queries.py                              # 角色内几何观察与选择查询
│       │   ├── recovery.py                             # 恢复证据、写入阻断与历史保护
│       │   ├── scope.py                                # 观察作用域与恢复确认
│       │   ├── sessions.py                             # 原生工作区创建与恢复
│       │   └── state.py                                # 模型、几何与版本状态读回
│       ├── __init__.py                                 # 工作台包初始化
│       ├── bridge.py                                   # 本地桥接与角色绑定入口
│       ├── Init.py                                     # FreeCAD 应用初始化入口
│       ├── InitGui.py                                  # 工作台与角色绑定按钮
│       ├── package.xml                                 # 工作台包元数据
│       └── runtime.py                                  # FreeCAD 依赖、进程状态与共用工具
├── docs/                                               # 用户文档
│   └── README.zh-CN.md                                 # 中文说明
├── src/
│   └── REAgentFreeCAD/                                 # 本地 MCP 服务
│       ├── features/                                   # 建模接口适配
│       │   ├── __init__.py                             # 包初始化
│       │   ├── body.py                                 # Body 接口适配与证据检查
│       │   ├── boolean.py                              # 布尔接口适配与证据检查
│       │   ├── chamfer.py                              # 倒角接口适配与证据检查
│       │   ├── datum.py                                # 基准几何接口适配与证据检查
│       │   ├── draft.py                                # 拔模接口适配与证据检查
│       │   ├── execution.py                            # 特征接口共用的会话与证据检查
│       │   ├── extrude.py                              # 拉伸与凹槽接口适配与证据检查
│       │   ├── fillet.py                               # 圆角接口适配与证据检查
│       │   ├── hole.py                                 # 孔接口适配与证据检查
│       │   ├── loft.py                                 # 放样接口适配与证据检查
│       │   ├── mirror.py                               # 镜像接口适配与证据检查
│       │   ├── pattern.py                              # 阵列接口适配与证据检查
│       │   ├── pipe.py                                 # 扫掠接口适配与证据检查
│       │   ├── primitives.py                           # 基本几何接口适配与证据检查
│       │   ├── revolve.py                              # 旋转与旋转切除接口适配与证据检查
│       │   ├── sketch.py                               # 草图接口适配与证据检查
│       │   └── thickness.py                            # 厚度接口适配与证据检查
│       ├── workflow/                                   # 重建管理流程
│       │   ├── __init__.py                             # 包初始化
│       │   ├── context.py                              # 会话状态、受控桥接调用与日志协调
│       │   ├── diagnostics.py                          # 原生日志诊断流程
│       │   ├── discovery.py                            # 接口能力与知识卡发现
│       │   ├── history.py                              # 会话内原生撤销与重做
│       │   ├── observation.py                          # Source 与 Target 观察证据适配
│       │   ├── session.py                              # 双窗口会话生命周期
│       │   └── validation.py                           # 核验事实、依赖版本与保存流程
│       ├── __init__.py                                 # 包初始化与延迟加载入口
│       ├── __main__.py                                 # Python 模块启动入口
│       ├── artifacts.py                                # 模型、日志等产物的命名与分配
│       ├── bootstrap.py                                # MCP 启动与依赖诊断
│       ├── client.py                                   # FreeCAD JSON Lines 桥接客户端
│       ├── contract.py                                 # 加载共享接口契约
│       ├── errors.py                                   # 公开错误结构与转换
│       ├── execution.py                                # 串行后台执行与结果查询
│       ├── journal.py                                  # 重建会话日志
│       ├── knowledge.py                                # Skill 与知识卡资源访问
│       ├── log_summary.py                              # 请求、结果与错误的精简日志
│       ├── results.py                                  # 桥接结果与错误的 JSON 安全转换
│       ├── server.py                                   # MCP stdio 服务与工具分派
│       ├── tools_schema.py                             # 公开 MCP 接口与参数结构
│       └── view_delivery.py                            # 视图图像整理与 MCP 交付
├── .gitignore                                          # 本地文件忽略规则
├── LICENSE                                             # MIT 许可证
├── pyproject.toml                                      # 项目元数据、依赖与启动入口
├── README.md                                           # 英文说明
└── uv.lock                                             # 锁定的依赖版本
```

## 许可证

除另有第三方许可声明的内容外，REAgent-FreeCAD 采用 [MIT License](../LICENSE)。
