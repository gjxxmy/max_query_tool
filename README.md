# MAX 收入报表 · 本地工具

## 公网部署

仓库包含 `render.yaml`，可在 Render 使用 Blueprint 部署免费 Python Web Service。也可从公开仓库创建 Web Service：分支 `codex/initial-import`，构建命令 `pip install -r requirements.txt`，启动命令 `python server.py --cloud`，健康检查 `/health`，选择 Free 方案与 Singapore 区域。设置仅服务端可见的 `MAX_COOKIE_SECRET`（至少 32 个随机字符，保持不变）；Render 自动提供 `PORT` 和 `RENDER_EXTERNAL_HOSTNAME`。其他平台还需显式设置 `MAX_PUBLIC_HOST` 并使用 HTTPS 反向代理。

公网版的账号按浏览器隔离。Report Key 与应用记录通过认证加密后保存为 Secure、HttpOnly、SameSite Cookie，不回传 Key 原文给页面，不建立公共账号列表；查询时云端服务会解密并向 AppLovin 发送 Key。每个浏览器首次使用需自行输入，Mac 钥匙串的已有账号不会迁移到云端。Cookie 最多保留一年，清除 Cookie、换浏览器或更换服务加密密钥后需重新保存；保存容量受 Cookie 限制，满时可临时输入 Key。不要导出或分享 Cookie。

下载记录同样校验浏览器归属，内存保留最多两小时；服务休眠重启或重新部署后旧下载链接失效，重新查询即可。免费 Render 服务闲置 15 分钟会休眠，首次再次访问可能需要约一分钟唤醒。公网地址提供通用查询入口，不共享任何用户已有账号和报表。未设置云端启动参数时仍使用原本 Mac 本机模式，且不需要安装云端依赖。

## 本机运行

双击本目录的 `start.command`，浏览器会自动打开 <http://127.0.0.1:8766>。保留启动窗口；关闭窗口或按 Control+C 停止服务。再次双击会打开已有服务。需要 macOS 自带或已安装的 Python 3，无需安装第三方依赖。

1. 首次输入 AppLovin **Report Key**（不是 SDK Key），填写账号名称，点击「保存并使用」。之后直接下拉选择，可保存多个账号。也可临时输入 Key 查询而不保存。
2. 输入或选择应用包名 / Bundle ID；已记录的应用自动带出平台。首次无法判断时选择一次平台，查询成功后记住。选择单日或时间段查询，填写 UTC 日期及报表类型。
3. 选择「展示与收益汇总」，页面直接显示总展示次数、预估收益、eCPM、插屏＋激励合计、广告类型汇总及逐日数据；单日或时间段均可一次查询，下载一份汇总 CSV。
4. 选择「按用户汇总」或「逐次展示明细」时，单日下载原始 CSV；时间段逐天获取并显示进度、每日结果，完成后可下载 ZIP，每天一个原始 CSV。ZIP 不进行跨日用户汇总。某天失败不影响其他天，可重试未成功日期或停止查询。

时间段包含开始与结束当天，一次最多 45 天，所有日期仍须在接口过去 45 天的窗口内。ZIP 附有「查询说明.txt」和「下载结果.txt」，明确列出缺失或下载失败的日期；下载中断或不完整的每日 CSV 不会加入 ZIP。链接过期时重新查询相应日期。文件由浏览器保存到下载目录或询问保存位置，CSV 可用 Excel / Numbers 打开。

日期默认为最近理论可用的 UTC 日期。用户级报表在 UTC 日结束 8 小时后提供，即北京时间次日 16:00 后。汇总报表可查询当天，但最近 1–2 小时可能不完整；对账建议选择完整 UTC 日。两种接口均可请求过去 45 天内的数据。

汇总模式调用 AppLovin 官方 Revenue Reporting API（`https://r.applovin.com/maxReport`），复用所选 Report Key，按 `day,ad_format` 统计，过滤 `platform` 及 `package_name`（包名 / Bundle ID）或 `store_id`（商店 ID）。不筛应用版本。展示次数为 MAX `impressions`，收益为 `estimated_revenue`，USD；eCPM 由匹配范围的总收益 ÷ 总展示 × 1000 计算，不平均各行 eCPM。未返回匹配记录的日期会明确列出，不自动补零。汇总 CSV 为工具根据接口结果生成的逐日、分类型数据，含 UTF-8 BOM，收益与 eCPM 保留 6 位小数，页面显示 2 位；不附加总计行，避免再次求和时重复计算。数值异常、过滤范围不符或可能截断的响应会报错，不显示部分合计。下载内容暂存内存 2 小时，关闭服务即清除。

`application` 和 `store_id` 二选一。Android 通常用包名，iOS 可以用 Bundle ID 或数字商店 ID。输入纯数字 Store ID 会自动切到 iOS。Android 包名与 iOS Bundle ID 格式可能相同，不能仅凭文字准确判断；工具根据已确认的记录自动选择，同一标识记录了多个平台时请手动选择。按用户汇总对应 `aggregated=true`，逐次展示对应 `false`。

保存的 Report Key 存入这台 Mac 的登录钥匙串，不写入源码、明文配置、浏览器存储或日志，也不会由设置接口回传到页面。账号名称和应用平台记忆保存在 `~/Library/Application Support/MAXRevenueReport/preferences.json`（仅当前用户可读写），其中没有密钥。临时输入但未保存的 Key 只用于当前请求。重启工具后，已保存账号和应用记录仍在。服务仅监听本机 127.0.0.1，查询发送至 AppLovin 官方 HTTPS 接口。单日 CSV 直接流式转发；ZIP 按天打包，较小文件使用内存，较大文件会短暂缓存在系统私有临时文件中，处理完自动删除，不保存在源码目录；用户主动下载的文件按浏览器设置保存。下载链接记录只保留在内存 2 小时，AppLovin 链接本身也可能更早过期；失效时重新查询。关闭工具后临时报表链接记录清空；保存的账号及应用平台记录仍保留。

查询与下载需要联网和有效 Report Key。工具不包含真实密钥或用户报表。部分网络或账户错误会显示统一提示，请检查平台、应用归属及 Report Key。用户级原始 CSV 不进行格式转换、预览或内容修改。

后续自动查询可直接向本机 `/api/summary` POST JSON：`profile_id`（从 `/api/preferences` 取得已保存账号引用）、`identifier_type`（`application` / `store_id`）、`identifier`、`platform`、`start_date`、`end_date`。响应含逐日 `rows`、分类型 `totals`、总计 `total`、插屏与激励合计 `fullscreen_total`、无记录日期 `empty_days`、查询时间与本机 CSV 下载地址；接口不返回 Report Key。

若默认端口占用，可在本目录运行：

```sh
python3 server.py --open --port 8767
```

[汇总报表官方文档](https://support.applovin.com/zh/max/reporting-apis/revenue-reporting-api) · [用户级报表官方文档](https://support.applovin.com/zh/max/reporting-apis/user-level-ad-revenue-api)
