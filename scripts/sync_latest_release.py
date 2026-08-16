#!/usr/bin/env python3
"""Sync the standalone customer release page from a private local source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".release-source.local.json"
VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-dev\.(\d+))?$")


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> str:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if check and result.returncode != 0:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout.strip() if capture else ""


def load_config() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            "缺少本机私有同步配置：.release-source.local.json\n"
            "该文件不会提交到 GitHub。"
        )
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def normalize_remote(url: str) -> str:
    url = url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    url = url.replace("git@github.com:", "https://github.com/")
    return url


def ensure_bound_source(source_dir: Path, expected_remote: str) -> None:
    if not (source_dir / ".git").exists():
        raise SystemExit(f"来源目录不是 Git 仓库：{source_dir}")
    origin = run(["git", "remote", "get-url", "origin"], cwd=source_dir, capture=True)
    if normalize_remote(origin) != normalize_remote(expected_remote):
        raise SystemExit(
            "来源项目远端不匹配，已停止同步：\n"
            f"  当前：{origin}\n"
            f"  需要：{expected_remote}"
        )


def version_key(tag: str) -> tuple[int, int, int, int]:
    match = VERSION_RE.match(tag)
    if not match:
        return (-1, -1, -1, -1)
    major, minor, patch, dev = match.groups()
    return (int(major), int(minor), int(patch), int(dev or 9999))


def latest_tag(source_dir: Path, pattern: str) -> str:
    tags = run(["git", "tag", "--list", pattern], cwd=source_dir, capture=True).splitlines()
    tags = [tag for tag in tags if VERSION_RE.match(tag)]
    if not tags:
        raise SystemExit("来源项目没有找到可同步的版本 tag。")
    return max(tags, key=version_key)


def worktree_roots(source_dir: Path) -> list[Path]:
    output = run(["git", "worktree", "list", "--porcelain"], cwd=source_dir, capture=True)
    roots: list[Path] = []
    for line in output.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ")).resolve()
            if path.exists():
                roots.append(path)
    if source_dir.resolve() not in roots:
        roots.insert(0, source_dir.resolve())
    return roots


def build_number(tag: str) -> str:
    match = VERSION_RE.match(tag)
    if not match or not match.group(4):
        raise SystemExit(f"当前同步脚本需要 dev 构建号 tag：{tag}")
    return match.group(4)


def release_family(tag: str) -> str:
    return tag.rsplit(".", 1)[0]


def find_artifact(source_dir: Path, tag: str, suffix: str) -> Path | None:
    build = build_number(tag)
    family = release_family(tag)
    candidates: list[Path] = []
    for root in worktree_roots(source_dir):
        release_root = root / "release"
        preferred = release_root / family / f"build-{build}"
        search_roots = [preferred, release_root]
        for search_root in search_roots:
            if search_root.exists():
                candidates.extend(search_root.rglob(f"*{build}*macos-arm64.{suffix}"))
                candidates.extend(search_root.rglob(f"*{tag}*macos-arm64.{suffix}"))
    candidates = [path for path in candidates if path.is_file() and not path.name.startswith(".")]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_dmg_from_zip(zip_path: Path, tag: str) -> Path:
    output = Path(tempfile.gettempdir()) / f"CN-ES-Interpreter-{tag}-macos-arm64.dmg"
    if output.exists():
        output.unlink()
    with tempfile.TemporaryDirectory(prefix="cn-es-release-dmg-") as temp_dir:
        stage = Path(temp_dir) / "CN-ES Interpreter"
        stage.mkdir(parents=True)
        run(["ditto", "-x", "-k", str(zip_path), str(stage)])
        app_candidates = sorted(stage.glob("*.app"))
        if not app_candidates:
            raise SystemExit(f"ZIP 中没有找到 .app：{zip_path}")
        app_path = app_candidates[0]
        target_app = stage / "CN-ES Interpreter.app"
        if app_path != target_app:
            app_path.rename(target_app)
        (stage / "Applications").symlink_to("/Applications")
        run(
            [
                "hdiutil",
                "create",
                "-volname",
                "CN-ES Interpreter",
                "-srcfolder",
                str(stage),
                "-format",
                "UDZO",
                "-ov",
                str(output),
            ]
        )
    run(["hdiutil", "verify", str(output)])
    return output


def copy_release_files(tag: str, dmg: Path, zip_path: Path | None) -> tuple[str, str | None]:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    dmg_target = dist / f"CN-ES-Interpreter-{tag}-macos-arm64.dmg"
    if dmg.resolve() != dmg_target.resolve():
        shutil.copy2(dmg, dmg_target)
    zip_target = None
    if zip_path:
        zip_target = dist / f"CN-ES-Interpreter-{tag}-macos-arm64.zip"
        if zip_path.resolve() != zip_target.resolve():
            shutil.copy2(zip_path, zip_target)
    return sha256(dmg_target), sha256(zip_target) if zip_target else None


def load_user_guide(source_dir: Path) -> str:
    guide_path = source_dir / "docs" / "USER_GUIDE.md"
    if not guide_path.exists():
        return ""

    guide = guide_path.read_text(encoding="utf-8").strip()
    (ROOT / "USER_GUIDE.md").write_text(guide + "\n", encoding="utf-8")

    lines = guide.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    return re.sub(
        r"^(#{1,5}) ",
        lambda match: "#" * (len(match.group(1)) + 1) + " ",
        body,
        flags=re.MULTILINE,
    )


def write_text_files(tag: str, dmg_sha: str, zip_sha: str | None, user_guide: str) -> None:
    dmg_name = f"CN-ES-Interpreter-{tag}-macos-arm64.dmg"
    zip_name = f"CN-ES-Interpreter-{tag}-macos-arm64.zip"
    release_url = f"https://github.com/waitlogic-ops/CN-ES-Interpreter-Release/releases/download/{tag}"

    readme = f"""# CN-ES Interpreter 同声传译

CN-ES Interpreter 是一款面向会议、培训、商务沟通和现场展示的实时同声传译工具。它支持中文、西班牙语和英语场景，把实时字幕、目标语言播报、方向控制和多端观看整合在同一个工作台里。

## 下载

当前版本：`{tag}`

- 推荐下载：[`{dmg_name}`]({release_url}/{dmg_name})
"""
    if zip_sha:
        readme += f"- 备用 ZIP：[`{zip_name}`]({release_url}/{zip_name})\n"
    readme += f"""- 完整发布页：[`{tag}`](https://github.com/waitlogic-ops/CN-ES-Interpreter-Release/releases/tag/{tag})
- 文件校验：[`CHECKSUMS.txt`](CHECKSUMS.txt)

## 产品预览

![CN-ES Interpreter 产品介绍](assets/cn-es-interpreter-brief-01.png)

![CN-ES Interpreter 使用流程](assets/cn-es-interpreter-brief-02.png)

![CN-ES Interpreter 同传模式](assets/cn-es-interpreter-brief-03.png)

![CN-ES Interpreter 单向翻译](assets/cn-es-interpreter-brief-04.png)

![CN-ES Interpreter 会议同传工作台](assets/cn-es-interpreter-brief-05.png)

## 适合做什么

- 跨语言商务会议和外贸沟通
- 双语课堂、培训和采访字幕
- 现场演示、移动旁听和大屏投放
- 需要连续字幕、语音播报和多端同步的沟通场景

## 主要能力

- **实时同传**：持续识别说话内容，生成双语字幕。
- **单向翻译**：在演示、培训或嘈杂环境中手动锁定输入方向。
- **多端同步**：Mac 主控开始后，手机、Web 和大屏可以同步查看字幕。
- **语音与字幕分离**：可按现场需要关闭播报，只保留字幕和记录。
- **本地配置**：翻译、语音和大模型服务配置保留在本机。

## 安装

1. 下载并打开 `{dmg_name}`。
2. 将 `CN-ES Interpreter.app` 拖入“Applications / 应用程序”文件夹。
3. 首次运行时，根据 macOS 提示允许打开，并授予麦克风权限。
4. 按应用内提示配置所需的翻译、语音或会议纪要服务。

## 使用说明

{user_guide if user_guide else "使用说明会随来源项目的 `docs/USER_GUIDE.md` 同步更新。"}

## 介绍素材

上方 5 张产品介绍图也已放在发布附件中，可单独下载。
"""
    (ROOT / "README.md").write_text(readme, encoding="utf-8")

    notes = f"""# CN-ES Interpreter {tag}

本版本提供 macOS Apple Silicon 版本下载，适用于中文、西班牙语和英语场景下的会议同传、培训字幕、商务沟通和现场展示。

## 下载

- 推荐下载：`{dmg_name}`
"""
    if zip_sha:
        notes += f"- 备用下载：`{zip_name}`\n"
    notes += f"""- 平台：macOS Apple Silicon / arm64
- DMG SHA-256：`{dmg_sha}`
"""
    if zip_sha:
        notes += f"- ZIP SHA-256：`{zip_sha}`\n"
    notes += """
## 功能亮点

- 实时识别并生成双语字幕。
- 支持自然对话的同传模式，也支持演示、培训场景下的单向翻译。
- 支持 Mac 主控，手机、Web 和大屏同步查看字幕。
- 支持按需关闭语音播报，只保留字幕和会议记录。
- 翻译、语音和大模型服务配置保留在本机。

## 安装提醒

下载 DMG 后，将 `CN-ES Interpreter.app` 拖入“Applications / 应用程序”文件夹。首次运行时，请根据 macOS 提示允许打开，并授予麦克风权限。

## 附件

本发布页附件只包含 macOS DMG 安装包和 ZIP 备用包。
"""
    notes_path = ROOT / f"RELEASE_NOTES_{tag}.md"
    for old in ROOT.glob("RELEASE_NOTES_v*.md"):
        if old != notes_path:
            old.unlink()
    notes_path.write_text(notes, encoding="utf-8")

    checksums = [f"{dmg_sha}  {dmg_name}"]
    if zip_sha:
        checksums.append(f"{zip_sha}  {zip_name}")
    (ROOT / "CHECKSUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")


def publish(tag: str, dmg: Path, zip_path: Path | None) -> None:
    notes = ROOT / f"RELEASE_NOTES_{tag}.md"
    run(
        [
            "git",
            "add",
            "-A",
            ".gitignore",
            "README.md",
            "USER_GUIDE.md",
            "CHECKSUMS.txt",
            str(notes.name),
            "RELEASE_NOTES_*.md",
            "assets",
            "scripts/sync_latest_release.py",
        ],
        cwd=ROOT,
    )
    if run(["git", "status", "--short"], cwd=ROOT, capture=True):
        run(["git", "commit", "-m", f"同步 {tag} 发布页"], cwd=ROOT)
        run(["git", "push"], cwd=ROOT)

    release_exists = subprocess.run(
        ["gh", "release", "view", tag, "--repo", "waitlogic-ops/CN-ES-Interpreter-Release"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    if not release_exists:
        run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                "waitlogic-ops/CN-ES-Interpreter-Release",
                "--title",
                f"CN-ES Interpreter {tag}",
                "--notes-file",
                str(notes),
                "--prerelease",
            ],
            cwd=ROOT,
        )
    else:
        run(["gh", "release", "edit", tag, "--repo", "waitlogic-ops/CN-ES-Interpreter-Release", "--notes-file", str(notes)], cwd=ROOT)

    upload = [
        "gh",
        "release",
        "upload",
        tag,
        str(dmg),
        "--repo",
        "waitlogic-ops/CN-ES-Interpreter-Release",
        "--clobber",
    ]
    if zip_path:
        upload.insert(4, str(zip_path))
    run(upload, cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", help="Version tag to sync, e.g. v0.10.0-dev.086")
    parser.add_argument("--publish", action="store_true", help="Commit, push and update GitHub Release")
    parser.add_argument("--fetch-tags", action="store_true", help="Fetch source tags before selecting the version")
    args = parser.parse_args()

    config = load_config()
    source_dir = (ROOT / config["source_local_path"]).resolve()
    ensure_bound_source(source_dir, config["source_remote"])
    if args.fetch_tags:
        run(["git", "fetch", "origin", "--tags"], cwd=source_dir)

    tag = args.version or latest_tag(source_dir, config["release_tag_pattern"])
    zip_path = find_artifact(source_dir, tag, "zip")
    dmg_path = find_artifact(source_dir, tag, "dmg")
    existing_dmg = ROOT / "dist" / f"CN-ES-Interpreter-{tag}-macos-arm64.dmg"
    if not dmg_path and existing_dmg.exists():
        dmg_path = existing_dmg
    if not dmg_path:
        if not zip_path:
            raise SystemExit(f"没有找到 {tag} 的 DMG 或 ZIP 成果包。")
        dmg_path = create_dmg_from_zip(zip_path, tag)

    dmg_sha, zip_sha = copy_release_files(tag, dmg_path, zip_path)
    dist_dmg = ROOT / "dist" / f"CN-ES-Interpreter-{tag}-macos-arm64.dmg"
    dist_zip = ROOT / "dist" / f"CN-ES-Interpreter-{tag}-macos-arm64.zip"
    user_guide = load_user_guide(source_dir)
    write_text_files(tag, dmg_sha, zip_sha, user_guide)

    print(f"version={tag}")
    print(f"dmg={dist_dmg}")
    if zip_sha:
        print(f"zip={dist_zip}")
    if args.publish:
        publish(tag, dist_dmg, dist_zip if zip_sha else None)
        print("published=true")
    else:
        print("published=false")
        print("Run with --publish to push and update GitHub Release.")


if __name__ == "__main__":
    main()
