# 来源项目绑定

这个仓库是 **CN-ES Interpreter 的独立发布页项目**，只存放对外下载页、产品说明、校验信息和产品介绍图片。

## 固定来源

- 来源项目：`CN-ES-Interpreter`
- 本地来源目录：`../CN-ES-Interpreter`
- 来源 GitHub 仓库：`https://github.com/waitlogic-ops/CN-ES-Interpreter.git`
- 发布页 GitHub 仓库：`https://github.com/waitlogic-ops/CN-ES-Interpreter-Release`

本地源码目录是 `/Users/spoffish/Documents/软件开发/CN-ES-Interpreter`，它的 `origin` 远端必须是 `waitlogic-ops/CN-ES-Interpreter`。同步脚本会强制检查这个远端，防止把其他项目成果发布到这里。

## 边界

本仓库不放源码、不放构建脚本、不放测试、不放 `.env`、不放内部开发资料。安装包只作为 GitHub Release 附件上传。

## 同步方式

从本仓库执行：

```bash
python3 scripts/sync_latest_release.py --version v0.10.0-dev.086 --publish
```

不传 `--version` 时，脚本会从来源项目 tag 中选择最新版本。

同步流程：

1. 校验来源项目远端必须是 `waitlogic-ops/CN-ES-Interpreter`。
2. 查找对应版本的 macOS arm64 成果包。
3. 若只有 ZIP、没有 DMG，则从 ZIP 生成 DMG。
4. 更新 README、Release Notes 和 CHECKSUMS。
5. 提交并推送发布页仓库。
6. 创建或更新 GitHub Release，并上传 DMG、ZIP 与产品介绍图。
