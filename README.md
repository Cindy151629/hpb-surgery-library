# 肝胆胰外科文献与视频笔记库

日常使用只需打开 `肝胆胰外科_文献与视频笔记库.html`。页面内嵌累计主记录和全部内容笔记，无需安装软件、R、Obsidian、Zotero 或模型 API。每条文献可展开研究问题、研究设计、具体结果、解读、局限及来源定位。浏览器个人笔记与收藏保存在本地；更换浏览器或文件位置前先导出个人备份。

## 本轮交付与实际状态

原输入的 365 个 ID 均保留，另纳入 3 份指南：NEW-LT-001、NEW-LT-002、NEW-NICE-001。文献和学会网页、具体视频、课程、合集及待核线索分开计数；实时数字见网页和 `data/reports/audit-summary.json`。每份笔记标明摘要、正文重点或资料受限；资料受限不能算全文精读。所有原始输入的备份与私密抓取证据留在本地工作区，不进入公开包。

- 代码：实现了固定检索、去重、候选分类、题录变更检查、链接检查、累计状态、页面构建、云端发布和失败恢复。
- 本地验证：最终真实更新完成64项来源查询，来源失败0项；全部368条主记录的笔记哈希保持一致。81项Python测试及9项JavaScript测试通过。本地HTTP浏览器测试另有逐项记录；准确结果见 `data/reports/tests.json`、`final-local-routine-run.json`、`browser-tests.json`。
- 云端部署：专用仓库 `Cindy151629/hpb-surgery-library` 与 Pages 已部署；两次独立云端运行均通过检索、90项测试、发布与外部校验。公开地址为 https://cindy151629.github.io/hpb-surgery-library/ 。
- 周调度：默认分支工作流已由 GitHub 确认为 active。排期每周一北京时间 09:17，UTC 周一 01:17。
- 真实定时运行：尚未发生可观察的本项目运行；不能用本地执行或 YAML 文件替代。
- 视频：原站链接与概括已整理。页面、播放、嵌入、完整观看是四个独立维度。当前没有通过实际播放/嵌入测试的记录，故不展示未核验播放器，也不保证当前网络和账号能播放全部视频。原站可能要求登录或订阅。
- 双击入口：HTML 已生成并内嵌完整数据。自动化浏览器安全策略拒绝 file:// 导航，因此真实 file:// 浏览及跨域同步验收尚未完成；没有绕过该限制。本地 HTTP 功能已测试。

## 文件

- `src/index.html`、`src/style.css`、`src/app.js`：维护源。
- `data/records.json`：唯一正式主记录；`data/candidates.json`：尚未完成人工阅读/内容核验的候选，不混入精读数量。
- `data/additions.json`、`data/curated_overrides.json`：本轮新增指南与有证据的字段修订。
- `config/taxonomy.json`、`config/sources.json`：专题、别名、来源、纳排规则与检索版本。
- `data/state.json`、`data/runs/`、`data/checkpoints/`：累计成功进度、查询分页、失败与续跑状态。
- `data/reports/逐条核验报告.md`、`专题覆盖与缺口报告.md`：两份可读报告。网页也可逐条查看并导出 JSON 报告。`fulltext-link-sweep.json` 记录179个独立全文链接的真实请求；`page-identity-reclassification.json` 记录使用同一响应缓存重新分类的结果，解析时间不冒充联网时间。
- `.github/workflows/weekly.yml`：独立云端工作流，不依赖用户电脑或 Codex 常开。
- `PUBLICATION_SCOPE.md` 与 `PUBLICATION_MANIFEST.json`：已获授权的实际公开范围、文件散列和字节数。

## 本地构建和检查

在本目录使用 Python 3.13 和 Node 22 或更高兼容版本：

```sh
python3 -m pip install -r requirements.txt
python3 scripts/build.py
python3 -m unittest discover -s tests -v
node --test tests/core.test.cjs
python3 -m http.server 8767 --bind 127.0.0.1
```

默认构建只读取累计主数据。`build.py --migrate` 仅用于本地原始包迁移，需要未公开的 `data/inherited.json` 和 `work/hpb` 证据；不可在云端定期运行。日常更新不会重写人工阅读笔记。

真实手动更新：

```sh
python3 scripts/update.py
```

首次历史补检或指定主题补跑：

```sh
python3 scripts/discovery.py --initial
python3 scripts/discovery.py --initial --topics liver-resection
python3 scripts/build.py
```

程序使用 Europe PMC 与 PubMed，不限 OA；首次补检偏重指南、共识、定义、随机试验和系统综述。例行检索使用入库/修改日期，成功进度前重叠21天，每月回看90天，并轮转历史薄弱板块。EPMC 使用游标和磁盘检查点；PubMed 最多10,000条，触及上限会明确失败并保留缺口，需要拆分查询，不能宣称查全。失败来源不推进成功水位。 失败查询会保留原式、版本、指纹和日期窗口；单来源补跑只合并同一有效周期的成功证据。首页将实际检索完成时间与各来源最早覆盖水位分开显示，避免旧进度冒充本轮完成。

SAGES RSS、Stanford 官方目录有自动巡查；目录返回并不证明覆盖平台全部历史视频。中文期刊、学会、JOMI、webop 等人工补查范围见来源配置；Embase、Web of Science、CNKI 付费检索未完成授权检索。候选保留为待核信息，常规程序不会凭题名生成医学结论或精读笔记。

## 云端部署与手动补跑

用户已确认 PUBLICATION_SCOPE.md 所列公开范围和 Cindy151629 账户，专用仓库已建立。只上传 `public-review/` 的白名单内容；原始附件、全文与个人笔记仍留在本地。常规更新使用 GitHub 自动提供的工作流凭据，无需配置模型 API 密钥。

手动补跑：打开 https://github.com/Cindy151629/hpb-surgery-library/actions/workflows/weekly.yml ，点击 Run workflow，选择 main。已安装 GitHub CLI 时也可运行：

```sh
gh workflow run weekly.yml --repo Cindy151629/hpb-surgery-library --ref main
```

迁移到其他已授权目标时，在目标默认分支的 `config/deployment.json` 设置：

- `repository`：实际 OWNER/REPO。
- `https_base_url`：实际 GitHub Pages HTTPS 根地址，保留结尾斜线。
- `public_scope_approved`：仅在用户已批准该具体范围后改为 true。
- `status_url`：`https://raw.githubusercontent.com/OWNER/REPO/BRANCH/data/publication-status.json`。
- `runtime_status_url`：同一仓库、同一源码分支的 `data/runtime-status.json` raw HTTPS 地址。
- `schedule_enabled`：只有检查目标工作流状态确实为 active 后才能改为 true；不能凭配置文件存在设为 true。

在仓库 Settings → Pages 选择 GitHub Actions，确保仓库允许所需的 contents、pages、id-token 权限。将工作流放在默认分支，首次用 Actions → HPB weekly discovery and publication → Run workflow 手动验收。至少进行两次独立云端运行，确认第二个 runner 读取之前的累计数据和成功水位。

程序先保存实际开始状态，再检索/检查/构建/测试；部署后从公开 HTTPS 重新读取 HTML 和 JSON，比对本次制品字节、版本、校验值，并检查 Origin:null 的 CORS 响应。通过后才记录最近成功发布；网页独立读取两个状态回执，不把发布失败隐藏在旧网页中。

### 失败恢复

经外部验证成功的四个站点文件保存在专用 `release-good` 分支的不可变 commit 中。它与源码和累计候选分开。新发布失败时，只使用运行开始时固定的成功 commit 恢复，并重新校验 HTTPS 字节。首次没有成功版本会明确记为无可用回滚。

更新步骤保留75分钟上限，为120分钟任务总时限留出失败保存和恢复时间。异常时 `always()` 步骤提交成功来源进度、累计候选和检查点；推送也失败时，保留仅含允许公开 JSON 的 Actions 制品90天。骤停或平台级失效仍可能只留下 running 状态；首页显示运行开始时间，过期记录应查看 Actions 日志并手动补跑，不把它显示为零新增。

人工恢复前先确认目标版本并保留当前数据；可用同一恢复工具执行：

```sh
python3 scripts/cloud_recovery.py restore --directory recovery-site
python3 scripts/deploy_check.py --url ACTUAL_HTTPS_URL --expected-dir recovery-site --rollback
```

第二行只校验实际已经部署的恢复版本，不负责部署；不得把校验命令误当发布操作。常规工作流已包含 Pages 恢复部署步骤。

### 实际云端验收与剩余限制

2026年9月15日北京时间00:24—00:32完成两次独立云端验收，实际记录：

- 第一次：https://github.com/Cindy151629/hpb-surgery-library/actions/runs/34868308004 。64项来源查询全部完成，新增17条候选、修订7条候选题录。
- 第二次：https://github.com/Cindy151629/hpb-surgery-library/actions/runs/34868931077 。从第一次保存的累计提交启动，64项来源查询全部完成，新增0条、修订0条，去重有效；64项来源水位继续推进。
- 两次均通过81项Python及9项JavaScript测试、Pages部署、公开HTML/JSON字节一致与Origin:null响应校验。368条正式记录及原有阅读笔记哈希均保持一致，新增候选累计保留。第二次读取并校验了上一版可回滚制品。
- 最终核实版本 `hpb-461d9ce9658ee2fa`，最近成功检索北京时间2026年09月15日00:31:20，最近成功发布00:32:02。最终发布时间及版本以网页实时回执为准。
- 周工作流在默认分支为active。两次验收事件均为workflow_dispatch；尚未观察到真实schedule事件，下一次计划为2026年9月21日09:17北京时间。GitHub定时任务可能延迟或暂停；查看Actions失败记录并可手动补跑。
- 在线页面已显示云端读取校验通过、检索及发布时间、完整笔记章节。追加交互检查出现浏览器控制超时。真实file://与手机尺寸验收尚未完成；请求带Origin:null时返回允许跨域，只证明服务响应，不能替代真实file://测试。

完整证据见 `data/reports/cloud-acceptance.json` 和 `data/reports/云端自动更新验收报告.md`。初次提交曾因GitHub不允许在job环境中使用runner.temp而失败，已改为在运行步骤初始化；该失败历史保留，随后两次完整验收通过。未启用付费服务。


浏览器的个人笔记不进入任何云端请求。日常浏览、搜索、筛选、播放和常规更新程序均不调用大模型。复杂科学结论由专业复核后再并入正式笔记。
